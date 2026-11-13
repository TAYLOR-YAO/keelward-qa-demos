"""HTTP front door for the demo runner on Cloud Run.

POST /run {"RUN_ID": "...", "SUITE": "web|api", "VARIANT": "...", "BROWSERS": "..."} starts run.py in the
background and answers 202 straight away; the run reports its own progress.
POST /nightly (Cloud Scheduler, once a night) runs the four public demos one after another and adds each
result to the 30-night trend shown on the website.
Cloud Run's sign-in check (IAM) decides who may call this; the service is not public.
"""
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
SUITES = {"web": {"stable", "regression", "deep-stable", "deep-regression"}, "api": {"smoke", "full", "deep"}, "rag": {"good", "regressed"}}
BROWSERS = {"chromium", "firefox", "webkit", "mobile"}
NIGHTLY = [("web", "stable"), ("web", "regression"), ("api", "smoke"), ("api", "full")]
RUN_ID = re.compile(r"^[A-Za-z0-9]{20}$")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"ok": True})

    def do_POST(self):
        if self.path == "/nightly":
            threading.Thread(target=nightly, daemon=True).start()
            return self._send(202, {"started": "nightly"})
        if self.path != "/run":
            return self._send(404, {"error": "not-found"})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 2000)) or b"{}")
        except ValueError:
            return self._send(400, {"error": "json"})
        run_id, suite, variant = str(body.get("RUN_ID", "")), str(body.get("SUITE", "")), str(body.get("VARIANT", ""))
        if not RUN_ID.match(run_id) or variant not in SUITES.get(suite, ()):
            return self._send(400, {"error": "invalid"})
        browsers = ",".join(b for b in str(body.get("BROWSERS", "")).split(",") if b in BROWSERS)
        env = {**os.environ, "RUN_ID": run_id, "SUITE": suite, "VARIANT": variant, "BROWSERS": browsers, "RESULTS_DIR": f"/tmp/results-{run_id}"}
        proc = subprocess.Popen([sys.executable, str(HERE / "run.py")], cwd=HERE, env=env)
        threading.Thread(target=proc.wait, daemon=True).start()  # reap it when it ends
        self._send(202, {"started": run_id})

    def log_message(self, fmt, *args):
        print(f"[runner] {self.address_string()} {fmt % args}")


def nightly():
    """The four public demos, one after another, each recorded as a normal run plus a trend entry."""
    import secrets
    import string
    from datetime import datetime, timedelta, timezone

    from google.cloud import firestore

    env_name = os.environ.get("DEMO_ENV", "dev")
    db = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "keelward-web-50bc7"))
    for suite, variant in NIGHTLY:
        run_id = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
        try:
            db.collection("demoRuns").document(run_id).set({
                "suite": suite, "variant": variant, "env": env_name, "nightly": True, "status": "starting",
                "createdAt": firestore.SERVER_TIMESTAMP, "expiresAt": datetime.now(timezone.utc) + timedelta(days=90),
            })
            env = {**os.environ, "RUN_ID": run_id, "SUITE": suite, "VARIANT": variant, "NIGHTLY": "1", "BROWSERS": "",
                   "RESULTS_DIR": f"/tmp/results-{run_id}"}
            subprocess.run([sys.executable, str(HERE / "run.py")], cwd=HERE, env=env, timeout=300)
        except Exception as e:  # one failed demo must not stop the others
            print(f"[runner] nightly {suite}/{variant} failed: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"[runner] listening on {port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
