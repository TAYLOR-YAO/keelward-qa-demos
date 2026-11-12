"""pytest plugin: publishes each test's status, steps, screenshots and errors while the run is in progress."""
import json
import os
import re
import time
from contextlib import contextmanager

import pytest

from .sink import make_sink
from .stamp import stamp


def key_of(item):
    return re.sub(r"[^A-Za-z0-9_]", "_", item.name)[:80]


BROWSER_LABELS = {"chromium": "Chrome", "firefox": "Firefox", "webkit": "Safari (WebKit)", "mobile": "Phone (Pixel 7)"}


def browser_of(item):
    params = getattr(getattr(item, "callspec", None), "params", {}) or {}
    return params.get("browser", "")


def describe(item):
    m = item.get_closest_marker("requirement")
    meta = dict(m.kwargs) if m else {}
    b = browser_of(item)
    suffix = f" · {BROWSER_LABELS.get(b, b)}" if b else ""
    return {
        "req": meta.get("id", ""),
        "browser": b,
        "title": {"en": meta.get("en", item.name) + suffix, "fr": meta.get("fr", meta.get("en", item.name)) + suffix},
        "expected": {"en": meta.get("expected_en", ""), "fr": meta.get("expected_fr", "")},
        "severity": meta.get("severity", "Medium"),
        "smoke": item.get_closest_marker("smoke") is not None,
        "file": item.location[0],
        "line": (item.location[1] or 0) + 1,
    }


def now_ms():
    return int(time.time() * 1000)


def _writer(config):
    """Under pytest-xdist only the worker running a test reports it; otherwise this process does."""
    if hasattr(config, "workerinput"):
        return True
    try:
        n = config.getoption("numprocesses")
    except ValueError:
        n = None
    return not n


def pytest_configure(config):
    config._kl_sink = make_sink()
    config._kl_writer = _writer(config)


def pytest_collection_finish(session):
    """With KL_ANNOUNCE=1 (a --collect-only pass before the run) list every test as queued."""
    if os.environ.get("KL_ANNOUNCE") != "1":
        return
    tests, order = {}, []
    for item in session.items:
        key = key_of(item)
        order.append(key)
        tests[key] = {**describe(item), "status": "queued", "steps": []}
    sink = session.config._kl_sink
    sink.run_update({"tests": tests, "order": order, "total": len(order)})
    sink.flush()


def pytest_unconfigure(config):
    sink = getattr(config, "_kl_sink", None)
    if sink:
        sink.flush()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    item._kl = {"phases": {}, "started": now_ms(), "steps": []}
    if item.config._kl_writer:
        item.config._kl_sink.test_update(key_of(item), {"status": "running", "startedAt": item._kl["started"]})


def _short_error(excinfo):
    e = excinfo.value
    text = str(e).strip().splitlines()[0] if str(e).strip() else ""
    if isinstance(e, AssertionError):
        return (text or "Assertion failed")[:400]
    name = type(e).__name__
    return f"{name}: {text}"[:400] if text else name


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    state = getattr(item, "_kl", None)
    if state is None:
        return
    state["phases"][rep.when] = rep
    if rep.failed and call.excinfo is not None and "error" not in state:
        state["error"] = _short_error(call.excinfo)
        state["trace"] = str(rep.longreprtext)[-1500:]
    if rep.when != "teardown" or not item.config._kl_writer:
        return
    phases = state["phases"]
    if any(p.failed for p in phases.values()):
        status = "failed"
    elif any(p.skipped for p in phases.values()):
        status = "skipped"
    else:
        status = "passed"
    fields = {"status": status, "durationMs": now_ms() - state["started"], "finishedAt": now_ms()}
    if status == "failed":
        fields["error"] = state.get("error", "Failed")
        fields["trace"] = state.get("trace", "")
    item.config._kl_sink.test_update(key_of(item), fields)
    _record(item, fields)


def _record(item, fields):
    """Final result of each test, kept locally for the traceability matrix (requirement -> test -> result)."""
    out = os.environ.get("RESULTS_DIR")
    if not out:
        return
    d = describe(item)
    row = {"req": d["req"], "requirement": d["title"]["en"], "browser": BROWSER_LABELS.get(d["browser"], d["browser"]), "severity": d["severity"],
           "test": item.nodeid, "status": fields["status"], "durationMs": fields["durationMs"], "error": fields.get("error", "")}
    try:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, f"final-{os.getpid()}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[keelward-live] result not recorded: {e}")


class Steps:
    """``with step("Open the store", "Ouvrir la boutique"):`` records a step, with a screenshot when a page is attached."""

    def __init__(self, item):
        self.item = item
        self.key = key_of(item)
        self.sink = item.config._kl_sink
        self.writer = item.config._kl_writer
        self.page = None
        self.n = 0
        self._details = None

    def detail(self, text):
        if self._details is not None:
            self._details.append(text)

    def _shot(self, n):
        if self.page is None or not self.writer:
            return None
        try:
            data = stamp(self.page.screenshot(type="png", timeout=5000), self.page.url, os.environ.get("RUN_ID") or "local")
        except Exception as e:
            print(f"[keelward-live] screenshot failed: {e}")
            return None
        return self.sink.upload(f"{self.key}-{n}.jpg", data, "image/jpeg")

    @contextmanager
    def __call__(self, en, fr=None):
        self.n += 1
        n, start, ok = self.n, time.monotonic(), True
        self._details = []
        try:
            yield self
        except BaseException:
            ok = False
            raise
        finally:
            step = {"n": n, "en": en, "fr": fr or en, "ok": ok, "ms": int((time.monotonic() - start) * 1000), "at": now_ms()}
            if self._details:
                step["detail"] = self._details[:6]
            shot = self._shot(n)
            if shot:
                step["shot"] = shot
            self._details = None
            if self.writer:
                self.sink.add_step(self.key, step)


@pytest.fixture
def step(request):
    return Steps(request.node)


@pytest.fixture
def live(request):
    """(sink, test key, whether this process reports) for fixtures that publish files such as videos."""
    return request.config._kl_sink, key_of(request.node), request.config._kl_writer
