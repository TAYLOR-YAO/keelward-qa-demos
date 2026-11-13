"""Live AI service (Cloud Run, private): the "Ask Keelward" website assistant and the AI playground.

It runs the same applications the AI demos test every night (apps/), so what visitors use is what is tested.
Only the website's functions may call it; they apply the visitor limits and keep the question log.

  POST /chat           {question, history}                      the website assistant (tuned release)
  POST /play/chat      {question}                               both releases, side by side, with judge scores
  POST /play/triage    {text}                                   both releases
  POST /play/extract   {text} or {image, mime}                  both releases (image: base64, not stored)
  POST /play/moderate  {image, mime} or {sample}                both releases
  POST /play/agent     {release, message, prior, state}         the booking agent on a simulated hotel
Every answer includes the AI usage of the request.
"""
import base64
import contextvars
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_UPDATE_WARNING_OPT_IN", "NO")

from apps import agent, ask_keelward, extract, triage, vision  # noqa: E402
from keelward_live import ai  # noqa: E402

RELEASES = ("good", "regressed")
MAX_IMAGE = 2_500_000
POOL = ThreadPoolExecutor(max_workers=16)
POLICY = ("Keelward policy: prices are not published; Keelward gives a clear, fixed price before work begins. "
          "Questions the website does not answer can be sent to contact@keelward.io.")
DECLINE = re.compile(r"(do not know|don't know|not (?:available|mentioned|shared)|ne sais pas|pas d.information|contact@keelward\.io)", re.I)


def both(fn):
    """Runs fn(release) for the tuned and regressed releases in parallel (usage is still counted)."""
    futures = {r: POOL.submit(contextvars.copy_context().run, fn, r) for r in RELEASES}
    out = {}
    for r, f in futures.items():
        try:
            out[r] = f.result(timeout=55)
        except Exception as e:  # one release failing must not hide the other
            out[r] = {"error": type(e).__name__}
    return out


def sources_of(r):
    return [{"page": s["page"], "heading": s["heading"], "url": s["url"]} for s in r["sources"]]


def chat(body):
    q = str(body.get("question", "")).strip()[:500]
    if not q:
        raise ValueError("question")
    history = [{"role": h.get("role"), "text": str(h.get("text", ""))[:600]} for h in body.get("history", [])[-6:] if isinstance(h, dict)]
    r = ask_keelward.ask(q, "good", history=history)
    return {"answer": r["answer"], "sources": sources_of(r), "declined": bool(DECLINE.search(r["answer"]))}


def judge_scores(question, r):
    """Groundedness and relevancy of one answer, scored by the same AI judge as the nightly tests."""
    from deepeval.metrics import AnswerRelevancyMetric, GEval
    from deepeval.test_case import LLMTestCase, SingleTurnParams

    judge = ai.Judge()
    # The assistant's own rules count as known facts: saying prices are not published, or suggesting the
    # contact address, is grounded even when the retrieved pages do not say it.
    case = LLMTestCase(input=question, actual_output=r["answer"], retrieval_context=r["context"] + [POLICY])
    metrics = {
        "Groundedness": GEval(name="Groundedness", threshold=0.7, model=judge, async_mode=False,
                              criteria="Check every factual statement in ACTUAL_OUTPUT against RETRIEVAL_CONTEXT. Paraphrases are fine. Penalize any detail "
                                       "the context does not state: prices, durations, percentages, client results, names, services or promises. "
                                       "A short, fully supported answer scores 1.",
                              evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.RETRIEVAL_CONTEXT]),
        "Answer relevancy": AnswerRelevancyMetric(threshold=0.7, model=judge, async_mode=False),
    }

    def run(item):
        name, m = item
        try:
            m.measure(case)
            return {"name": name, "score": round(float(m.score or 0), 2), "threshold": m.threshold, "passed": m.is_successful(), "reason": (m.reason or "")[:300]}
        except Exception as e:
            return {"name": name, "score": None, "threshold": m.threshold, "passed": False, "reason": f"Not scored ({type(e).__name__})"}

    return [f.result(timeout=50) for f in [POOL.submit(contextvars.copy_context().run, run, it) for it in metrics.items()]]


def play_chat(body):
    q = str(body.get("question", "")).strip()[:300]
    if not q:
        raise ValueError("question")

    def one(release):
        r = ask_keelward.ask(q, release)
        return {"answer": r["answer"], "sources": sources_of(r), "scores": judge_scores(q, r)}

    return both(one)


def play_triage(body):
    text = str(body.get("text", "")).strip()[:1500]
    if not text:
        raise ValueError("text")

    def one(release):
        raw = triage.classify(text, release)
        p = triage.parse(raw)
        return {"raw": raw[:800], "valid": triage.parse(raw, strict=True) is not None, "result": p.model_dump() if p else None}

    return both(one)


def _image(body):
    data = base64.b64decode(str(body.get("image", "")), validate=True)
    if len(data) > MAX_IMAGE:
        raise ValueError("image-size")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return data, "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return data, "image/webp"
    raise ValueError("image-type")


def play_extract(body):
    if body.get("image"):
        data, mime = _image(body)
        inv = {"kind": "upload", "data": data, "mime": mime}
    else:
        text = str(body.get("text", "")).strip()[:4000]
        if not text:
            raise ValueError("text")
        inv = {"kind": "text", "text": text}

    def one(release):
        raw = extract.extract(inv, release)
        p = extract.parse(raw)
        return {"raw": raw[:1200], "valid": extract.parse(raw, strict=True) is not None, "result": p.model_dump() if p else None}

    return both(one)


def play_moderate(body):
    if body.get("sample") in {s["id"] for s in vision.SAMPLES}:
        data = vision.sample_image(body["sample"])
    else:
        data, mime = _image(body)
        if mime != "image/png":  # the moderator takes PNG
            import io

            from PIL import Image

            buf = io.BytesIO()
            Image.open(io.BytesIO(data)).convert("RGB").save(buf, "PNG")
            data = buf.getvalue()

    def one(release):
        r, raw = vision.moderate(data, release)
        return {"raw": raw[:600], "result": r.model_dump() if r else None}

    return both(one)


def play_agent(body):
    release = body.get("release") if body.get("release") in RELEASES else "good"
    message = str(body.get("message", "")).strip()[:400]
    if not message:
        raise ValueError("message")
    prior = [{"role": t.get("role"), "text": str(t.get("text", ""))[:800]} for t in body.get("prior", [])[-8:] if isinstance(t, dict)]
    tools = agent.SimTools(body.get("state"))
    replies = agent.chat(tools, [message], release, prior=prior)
    return {"reply": replies[-1] if replies else "", "calls": tools.calls, "state": tools.state()}


ROUTES = {"/chat": chat, "/play/chat": play_chat, "/play/triage": play_triage, "/play/extract": play_extract,
          "/play/moderate": play_moderate, "/play/agent": play_agent}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"ok": True})

    def do_POST(self):
        fn = ROUTES.get(self.path)
        if not fn:
            return self._send(404, {"error": "not-found"})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 4_000_000)) or b"{}")
            with ai.collect() as rows:
                out = fn(body)
            self._send(200, {**out, "usage": ai.cost(rows)})
        except (ValueError, TypeError) as e:
            self._send(400, {"error": str(e) or "invalid"})
        except Exception as e:
            print(f"[live] {self.path} failed: {type(e).__name__}: {e}")
            self._send(502, {"error": "model"})

    def log_message(self, fmt, *args):
        print(f"[live] {fmt % args}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"[live] listening on {port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
