"""Runs one demo suite and publishes the run live.

Environment:
  SUITE    web (practice store, Playwright) or api (practice booking API)
  VARIANT  web: stable | regression        api: smoke | full
  RUN_ID   the website's run id; without it, results go to ./results only
"""
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from keelward_live.sink import make_sink

HERE = Path(__file__).parent
SUITES = {
    "web": {"dir": "suites/web_shop", "url": os.environ.get("SHOP_URL", "https://www.saucedemo.com"),
            "variants": {"stable": [], "regression": []}, "workers": "3"},
    "api": {"dir": "suites/booking_api", "url": os.environ.get("BOOKER_URL", "https://restful-booker.herokuapp.com") + "/ping",
            "variants": {"smoke": ["-m", "smoke"], "full": []}, "workers": "2"},
}
RESULTS = Path(os.environ.get("RESULTS_DIR", HERE / "results"))
TIME_LIMIT = int(os.environ.get("TIME_LIMIT", "150"))


def now_ms():
    return int(time.time() * 1000)


def summary_from(junit):
    root = ET.parse(junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    return {"total": total, "failed": failed, "skipped": skipped, "passed": total - failed - skipped}


def main():
    suite = os.environ.get("SUITE", "web")
    cfg = SUITES[suite]
    variant = os.environ.get("VARIANT") or next(iter(cfg["variants"]))
    if variant not in cfg["variants"]:
        sys.exit(f"Unknown variant {variant!r} for suite {suite!r}")
    os.environ["RELEASE"] = variant
    RESULTS.mkdir(parents=True, exist_ok=True)
    os.environ["RESULTS_DIR"] = str(RESULTS)
    sink = make_sink()
    started = now_ms()
    sink.run_update({"status": "running", "startedAt": started})
    sink.flush()

    # The practice site must be reachable, or a failure would wrongly look like a defect.
    try:
        r = requests.get(cfg["url"], timeout=10, headers={"User-Agent": "KeelwardQADemo/1.0 (+https://keelward.io/demo/)"})
        reachable = r.status_code < 500
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
        proc = subprocess.run([*base, "-n", cfg["workers"], f"--junitxml={junit}", f"--html={html}", "--self-contained-html",
                               "-p", "no:cacheprovider"], cwd=HERE, env=env, timeout=TIME_LIMIT)
        summary = summary_from(junit)
        reports = {}
        for name, path, ctype in [("html", html, "text/html; charset=utf-8"), ("junit", junit, "application/xml")]:
            if path.exists():
                reports[name] = sink.upload(path.name, path.read_bytes(), ctype)
        status = "passed" if proc.returncode == 0 else "failed" if summary["failed"] else "error"
        sink.run_update({"status": status, "finishedAt": now_ms(), "durationMs": now_ms() - started, "summary": summary, "reports": reports})
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
