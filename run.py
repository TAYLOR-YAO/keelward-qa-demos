"""Runs one demo suite and publishes the run live.

Environment:
  SUITE    web (practice store, Playwright) or api (practice booking API)
  VARIANT  web: stable | regression | deep-stable | deep-regression     api: smoke | full | deep
  BROWSERS deeper web demo only: chromium,firefox,webkit,mobile (default: all four)
  RUN_ID   the website's run id; without it, results go to ./results only
  NIGHTLY  1 for the scheduled nightly runs: also adds the result to the 30-night trend
"""
import csv
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from keelward_live.sink import make_sink

HERE = Path(__file__).parent
NOT_DEEP = ["-m", "not deep"]
SUITES = {
    "web": {"dir": "suites/web_shop", "url": os.environ.get("SHOP_URL", "https://www.saucedemo.com"),
            "variants": {"stable": NOT_DEEP, "regression": NOT_DEEP, "deep-stable": [], "deep-regression": []}},
    "api": {"dir": "suites/booking_api", "url": os.environ.get("BOOKER_URL", "https://restful-booker.herokuapp.com") + "/ping",
            "variants": {"smoke": ["-m", "smoke"], "full": NOT_DEEP, "deep": []}},
    # AI demos: applications under test that call Gemini (Vertex AI); no external site to check first.
    "rag": {"dir": "suites/ask_keelward", "url": None, "variants": {"good": [], "regressed": []}},
    "triage": {"dir": "suites/ticket_triage", "url": None, "variants": {"good": [], "regressed": []}},
    "extract": {"dir": "suites/invoice_extraction", "url": None, "variants": {"good": [], "regressed": []}},
    "agent": {"dir": "suites/booking_agent", "url": os.environ.get("BOOKER_URL", "https://restful-booker.herokuapp.com") + "/ping", "variants": {"good": [], "regressed": []}},
    "vision": {"dir": "suites/image_video", "url": os.environ.get("SHOP_URL", "https://www.saucedemo.com"), "variants": {"good": [], "regressed": []}},
}
ALL_BROWSERS = "chromium,firefox,webkit,mobile"
RESULTS = Path(os.environ.get("RESULTS_DIR", HERE / "results"))
TIME_LIMIT = int(os.environ.get("TIME_LIMIT", "150"))
LABELS = {"chromium": "Chrome", "firefox": "Firefox", "webkit": "Safari (WebKit)", "mobile": "Phone (Pixel 7)"}


def now_ms():
    return int(time.time() * 1000)


def summary_from(junit):
    root = ET.parse(junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    return {"total": total, "failed": failed, "skipped": skipped, "passed": total - failed - skipped}


def ai_usage(results):
    try:
        from keelward_live.ai import usage_summary
    except ImportError:
        return None
    return usage_summary(results)


def write_traceability(path):
    """Requirement -> test -> browser -> result -> defect, one row per test, for the downloadable matrix."""
    rows = []
    for f in sorted(RESULTS.glob("final-*.jsonl")):
        rows += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        return
    rows.sort(key=lambda r: (r["req"], r["browser"]))
    with open(path, "w", newline="", encoding="utf-8-sig") as out:
        w = csv.writer(out)
        w.writerow(["Requirement", "Requirement description", "Browser / device", "Severity", "Test", "Result", "Duration (s)", "Defect (actual result)"])
        for r in rows:
            w.writerow([r["req"], r["requirement"], r["browser"] or "-", r["severity"], r["test"], r["status"].upper(),
                        f"{r['durationMs'] / 1000:.1f}", r["error"] if r["status"] == "failed" else ""])


def main():
    suite = os.environ.get("SUITE", "web")
    cfg = SUITES[suite]
    variant = os.environ.get("VARIANT") or next(iter(cfg["variants"]))
    if variant not in cfg["variants"]:
        sys.exit(f"Unknown variant {variant!r} for suite {suite!r}")
    deep = variant.startswith("deep")
    os.environ["RELEASE"] = variant.replace("deep-", "") if suite == "web" else variant
    if suite == "web" and deep:
        os.environ["BROWSERS"] = os.environ.get("BROWSERS") or ALL_BROWSERS
    else:
        os.environ.pop("BROWSERS", None)
    browsers = [b for b in os.environ.get("BROWSERS", "").split(",") if b]
    RESULTS.mkdir(parents=True, exist_ok=True)
    os.environ["RESULTS_DIR"] = str(RESULTS)
    sink = make_sink()
    started = now_ms()
    sink.run_update({"status": "running", "startedAt": started})
    sink.flush()

    # The practice site must be reachable, or a failure would wrongly look like a defect.
    try:
        reachable = not cfg["url"] or requests.get(cfg["url"], timeout=10, headers={"User-Agent": "KeelwardQADemo/1.0 (+https://keelward.io/demo/)"}).status_code < 500
    except requests.RequestException:
        reachable = False
    if not reachable:
        sink.run_update({"status": "unavailable", "finishedAt": now_ms(), "durationMs": now_ms() - started})
        sink.flush()
        print("The practice site is not reachable; nothing was tested.")
        return 0

    select = cfg["variants"][variant]
    base = [sys.executable, "-m", "pytest", cfg["dir"], *select]
    env = {**os.environ}
    try:
        subprocess.run([*base, "--collect-only", "-p", "no:cacheprovider"], cwd=HERE, env={**env, "KL_ANNOUNCE": "1"},
                       check=True, timeout=60, stdout=subprocess.DEVNULL)
        junit, html = RESULTS / "junit.xml", RESULTS / "report.html"
        workers = str(max(2, len(browsers))) if browsers else ("4" if suite == "rag" else "3" if suite in ("web", "triage", "extract", "vision", "agent") else "2")
        dist = ["--dist", "loadgroup"] if browsers or suite == "vision" else []
        proc = subprocess.run([*base, "-n", workers, *dist, f"--junitxml={junit}", f"--html={html}", "--self-contained-html",
                               "-p", "no:cacheprovider"], cwd=HERE, env=env, timeout=TIME_LIMIT * (2 if deep else 1))
        summary = summary_from(junit)
        trace = RESULTS / "traceability.csv"
        write_traceability(trace)
        reports = {}
        for name, path, ctype in [("html", html, "text/html; charset=utf-8"), ("junit", junit, "application/xml"),
                                  ("traceability", trace, "text/csv; charset=utf-8")]:
            if path.exists():
                reports[name] = sink.upload(path.name, path.read_bytes(), ctype)
        status = "passed" if proc.returncode == 0 else "failed" if summary["failed"] else "error"
        fields = {"status": status, "finishedAt": now_ms(), "durationMs": now_ms() - started, "summary": summary, "reports": reports}
        usage = ai_usage(RESULTS)
        if usage:
            fields["ai"] = usage
        sink.run_update(fields)
        if os.environ.get("NIGHTLY") == "1":
            sink.trend_add(os.environ.get("DEMO_ENV", "dev"), suite, variant,
                           {"status": status, **summary, "durationMs": now_ms() - started, "runId": os.environ.get("RUN_ID", "")})
    except subprocess.TimeoutExpired:
        sink.run_update({"status": "error", "error": "time-limit", "finishedAt": now_ms(), "durationMs": now_ms() - started})
    except Exception as e:  # report, then fail the job
        sink.run_update({"status": "error", "error": type(e).__name__, "finishedAt": now_ms(), "durationMs": now_ms() - started})
        sink.flush()
        raise
    sink.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
