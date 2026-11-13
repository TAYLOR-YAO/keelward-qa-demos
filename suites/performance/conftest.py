"""Performance demo: a load test (Locust) on the Harbor & Co shop API, which runs next to it in the runner.

RELEASE=good tests the tuned release (connection pool, cache); RELEASE=regressed tests the "simplified
configuration" (one shared database connection). The load runs once, about 45 seconds; the tests then check
response times, errors and throughput for each stage of the run.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
RELEASE = os.environ.get("RELEASE") if os.environ.get("RELEASE") in ("good", "regressed") else "good"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def load():
    """Starts the shop API, runs the Locust load profile against it and returns every request it made."""
    port = free_port()
    app = subprocess.Popen([sys.executable, "-m", "apps.shop_api"], cwd=ROOT, env={**os.environ, "RELEASE": RELEASE, "PORT": str(port)})
    host = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                if requests.get(host + "/health", timeout=1).ok:
                    break
            except requests.RequestException:
                time.sleep(0.1)
        log = Path(tempfile.mkdtemp()) / "requests.json"
        started = time.time()
        subprocess.run([sys.executable, "-m", "locust", "-f", str(HERE / "locustfile.py"), "--headless", "--host", host, "--only-summary",
                        "--loglevel", "ERROR", "--stop-timeout", "3"], cwd=ROOT, env={**os.environ, "KL_PERF_LOG": str(log)},
                       timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rows = json.loads(log.read_text())
        return {"rows": rows, "seconds": round(time.time() - started, 1), "host": host}
    finally:
        app.terminate()
