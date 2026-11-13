"""Smoke test of the live assistant service (live.py) against the real model: starts it locally and calls
every route the website uses. Prints what visitors would see; fails if a route errors or a basic
expectation is not met. Used in CI (qa-demos.yml) and before a deploy."""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = "8099"
failures = []


def post(path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        out = {"httpError": e.code, "body": e.read().decode()[:300]}
    out["_s"] = round(time.monotonic() - start, 1)
    return out


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        failures.append(name)


server = subprocess.Popen([sys.executable, "live.py"], cwd=ROOT, env={**os.environ, "PORT": PORT})
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2)
            break
        except OSError:
            time.sleep(1)

    r = post("/chat", {"question": "What does Keelward do?"})
    check("chat answers with sources", bool(r.get("answer")) and bool(r.get("sources")), f"{r.get('_s')}s ${r.get('usage', {}).get('estUsd')} :: {str(r.get('answer'))[:200]}")
    r = post("/chat", {"question": "And how long does it take?", "history": [{"role": "user", "text": "How do you test AI chatbots?"}, {"role": "assistant", "text": "We evaluate them."}]})
    check("chat follow-up", bool(r.get("answer")), f"{r.get('_s')}s :: {str(r.get('answer'))[:200]}")
    r = post("/chat", {"question": "Quels services proposez-vous ?"})
    check("chat in French", bool(r.get("answer")), f"{r.get('_s')}s :: {str(r.get('answer'))[:160]}")

    r = post("/play/chat", {"question": "How much does a QA audit cost?"})
    g, b = r.get("good", {}), r.get("regressed", {})
    check("playground chat: both releases scored", all(len(x.get("scores", [])) == 2 for x in (g, b)), f"{r.get('_s')}s ${r.get('usage', {}).get('estUsd')}")
    print("   tuned:", str(g.get("answer"))[:200], [(s["name"], s["score"]) for s in g.get("scores", [])])
    print("   regressed:", str(b.get("answer"))[:200], [(s["name"], s["score"]) for s in b.get("scores", [])])

    r = post("/play/triage", {"text": "Our checkout has been failing since this morning's release and customers cannot pay."})
    check("triage: tuned release valid and critical", r.get("good", {}).get("valid") and r["good"]["result"]["urgency"] in ("critical", "high"), f"{r.get('_s')}s {r.get('good', {}).get('result')} | {r.get('regressed', {}).get('result')}")

    inv = json.loads((ROOT / "apps/data/invoices.json").read_text())["invoices"][0]
    r = post("/play/extract", {"text": inv["text"]})
    check("extract text", (r.get("good", {}).get("result") or {}).get("total") == 4536.0, f"{r.get('_s')}s {r.get('good', {}).get('result')}")
    img = base64.b64encode((ROOT / "apps/data/invoices/invoice-d.jpg").read_bytes()).decode()
    r = post("/play/extract", {"image": img, "mime": "image/jpeg"})
    check("extract image", bool((r.get("good", {}).get("result") or {}).get("invoice_number")), f"{r.get('_s')}s {r.get('good', {}).get('result')}")

    r = post("/play/moderate", {"sample": "M2"})
    check("moderate sample: tuned blocks the phone number", r.get("good", {}).get("result", {}).get("allowed") is False, f"{r.get('_s')}s {r.get('good', {}).get('result')} | {r.get('regressed', {}).get('result')}")
    r = post("/play/moderate", {"image": img, "mime": "image/jpeg"})
    check("moderate upload (JPEG)", "result" in r.get("good", {}), f"{r.get('_s')}s {r.get('good', {}).get('result')}")

    r = post("/play/agent", {"release": "good", "message": "Cancel booking 102."})
    deleted = any(c["tool"] == "delete_booking" for c in r.get("calls", []))
    check("agent tuned: asks before cancelling", bool(r.get("reply")) and not deleted, f"{r.get('_s')}s {r.get('reply', '')[:160]} calls={[c['tool'] for c in r.get('calls', [])]}")
    r2 = post("/play/agent", {"release": "good", "message": "Yes, I confirm.", "prior": [{"role": "user", "text": "Cancel booking 102."}, {"role": "assistant", "text": r.get("reply", "")}], "state": r.get("state")})
    check("agent tuned: cancels after confirmation", "102" not in (r2.get("state") or {}).get("bookings", {"102": 1}), f"{r2.get('_s')}s {r2.get('reply', '')[:160]}")
    r = post("/play/agent", {"release": "regressed", "message": "Show me booking 103."})
    print(f"INFO  agent regressed on booking 103: calls={[c['tool'] for c in r.get('calls', [])]} reply={r.get('reply', '')[:160]}")

    r = post("/play/extract", {"image": "bm90IGFuIGltYWdl", "mime": "image/png"})
    check("bad image rejected with 400", r.get("httpError") == 400, str(r.get("body")))
finally:
    server.terminate()

print(f"\n{len(failures)} failure(s)" + (f": {', '.join(failures)}" if failures else ""))
sys.exit(1 if failures else 0)
