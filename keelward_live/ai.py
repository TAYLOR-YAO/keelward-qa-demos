"""Gemini (Vertex AI) for the AI demos: one client for the applications under test and for the AI judge.

Authentication is the runner's own Google Cloud identity; no API key is used. Every call is recorded on
the current test step (model, tokens, time) and counted for the run's AI usage summary.
"""
import contextvars
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "keelward-web-50bc7")
LOCATION = os.environ.get("AI_LOCATION", "global")
APP_MODEL = os.environ.get("AI_APP_MODEL", "gemini-3.1-flash-lite")
JUDGE_MODEL = os.environ.get("AI_JUDGE_MODEL", "gemini-3.1-flash-lite")
# List prices per million tokens (input, output), for the usage estimate shown on the website.
PRICES = {"gemini-3.1-flash-lite": (0.25, 1.50), "gemini-3-flash-preview": (0.50, 3.00), "gemini-3-flash": (0.50, 3.00), "gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-flash-lite": (0.10, 0.40)}

_client = None
_lock = __import__("threading").Lock()


def client():
    """One shared client per process (created once, even when tests call the model from several threads)."""
    global _client
    with _lock:
        if _client is None:
            from google import genai

            _client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    return _client


_collector = contextvars.ContextVar("ai_usage", default=None)


@contextmanager
def collect():
    """Collects the usage of every model call made in this context (the live assistant reports it per request)."""
    rows = []
    token = _collector.set(rows)
    try:
        yield rows
    finally:
        _collector.reset(token)


def cost(rows):
    usd = 0.0
    for r in rows:
        pin, pout = PRICES.get(r["model"], (0.3, 2.5))
        usd += r["in"] / 1e6 * pin + r["out"] / 1e6 * pout
    return {"calls": len(rows), "inputTokens": sum(r["in"] for r in rows), "outputTokens": sum(r["out"] for r in rows), "estUsd": round(usd, 5)}


def _record(model, usage, ms, purpose):
    row = {"model": model, "in": getattr(usage, "prompt_token_count", 0) or 0,
           "out": (getattr(usage, "candidates_token_count", 0) or 0) + (getattr(usage, "thoughts_token_count", 0) or 0),
           "ms": ms, "purpose": purpose}
    rows = _collector.get()
    if rows is not None:
        rows.append(row)
    out = os.environ.get("RESULTS_DIR")
    if not out:
        return row
    Path(out).mkdir(parents=True, exist_ok=True)
    with open(Path(out) / f"ai-{os.getpid()}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def generate(contents, *, system=None, model=None, temperature=0.0, schema=None, step=None, purpose="app", max_tokens=1024, thinking="minimal"):
    """Text (or a JSON object when `schema` is a pydantic model or JSON schema) from Gemini, with retries."""
    from google.genai import types

    model = model or APP_MODEL
    config = types.GenerateContentConfig(
        system_instruction=system, temperature=temperature, max_output_tokens=max_tokens, seed=7,
        response_mime_type="application/json" if schema is not None else None, response_schema=schema,
    )
    if "2.5" in model:
        config.thinking_config = types.ThinkingConfig(thinking_budget=0)  # fast, cheap, repeatable
    elif model.startswith("gemini-3"):
        config.thinking_config = types.ThinkingConfig(thinking_level=thinking)
    last = None
    for attempt in range(4):
        start = time.monotonic()
        try:
            r = client().models.generate_content(model=model, contents=contents, config=config)
            ms = int((time.monotonic() - start) * 1000)
            row = _record(model, r.usage_metadata, ms, purpose)
            if step is not None and row:
                label = "AI judge" if purpose == "judge" else "Application"
                step.detail(f"{label}: {model} · {row['in']} → {row['out']} tokens · {ms} ms")
            return r.text or ""
        except Exception as e:  # rate limits and brief outages: back off and retry
            last = e
            if not any(code in str(e) for code in ("429", "500", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE")):
                raise
            time.sleep(1.5 * (attempt + 1))
    raise last


def usage_summary(results_dir):
    calls = tin = tout = 0
    cost = 0.0
    for f in Path(results_dir).glob("ai-*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            calls += 1
            tin += r["in"]
            tout += r["out"]
            pin, pout = PRICES.get(r["model"], (0.3, 2.5))
            cost += r["in"] / 1e6 * pin + r["out"] / 1e6 * pout
    return {"calls": calls, "inputTokens": tin, "outputTokens": tout, "estUsd": round(cost, 4)} if calls else None


class Judge:
    """DeepEval model backed by Gemini on Vertex AI, so every judge call is logged and counted like the rest."""

    def __new__(cls, step=None):
        from deepeval.models.base_model import DeepEvalBaseLLM

        class _Judge(DeepEvalBaseLLM):
            def __init__(self):
                self.step = step
                super().__init__(model=JUDGE_MODEL)

            def load_model(self):
                return JUDGE_MODEL

            def generate(self, prompt, schema=None, **kw):
                for attempt in range(2):
                    text = generate(prompt, model=JUDGE_MODEL, schema=schema, step=self.step, purpose="judge",
                                    max_tokens=8192, thinking=os.environ.get("AI_JUDGE_THINKING", "low"))
                    if schema is None:
                        return text
                    try:
                        return schema.model_validate_json(text)
                    except ValueError:  # an incomplete reply from the judge: ask once more
                        if attempt:
                            raise

            async def a_generate(self, prompt, schema=None, **kw):
                return self.generate(prompt, schema)

            def get_model_name(self):
                return f"{JUDGE_MODEL} (Vertex AI)"

        return _Judge()


def measure(step, metric, case, label):
    """Runs a DeepEval metric and records its score, threshold and the judge's reason on the step."""
    metric.measure(case)
    passed = metric.is_successful()
    step.score(label, round(float(metric.score or 0), 2), metric.threshold, passed, (metric.reason or "")[:500])
    return passed, metric
