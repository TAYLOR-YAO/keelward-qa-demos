"""HTTP front door for the demo runner on Cloud Run.

POST /run {"RUN_ID": "...", "SUITE": "web|api", "VARIANT": "..."} starts run.py in the background and
answers 202 straight away; the run reports its own progress. Cloud Run's sign-in check (IAM) decides
who may call this; the service is not public.
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
SUITES = {"web": {"stable", "regression"}, "api": {"smoke", "full"}}
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
        if self.path != "/run":
            return self._send(404, {"error": "not-found"})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 2000)) or b"{}")
        except ValueError:
            return self._send(400, {"error": "json"})
        run_id, suite, variant = str(body.get("RUN_ID", "")), str(body.get("SUITE", "")), str(body.get("VARIANT", ""))
        if not RUN_ID.match(run_id) or variant not in SUITES.get(suite, ()):
            return self._send(400, {"error": "invalid"})
        env = {**os.environ, "RUN_ID": run_id, "SUITE": suite, "VARIANT": variant, "RESULTS_DIR": f"/tmp/results-{run_id}"}
        proc = subprocess.Popen([sys.executable, str(HERE / "run.py")], cwd=HERE, env=env)
        threading.Thread(target=proc.wait, daemon=True).start()  # reap it when it ends
        self._send(202, {"started": run_id})

    def log_message(self, fmt, *args):
        print(f"[runner] {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"[runner] listening on {port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
