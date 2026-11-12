"""Where live events go: Firestore and Cloud Storage during a website run, local files otherwise.

Writes happen on one background thread per test process, so tests never wait for the network and
events from one process keep their order.
"""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class LocalSink:
    """Writes events as JSON lines and files under ./results (used in CI and on a laptop)."""

    def __init__(self, run_id="local"):
        self.dir = Path(os.environ.get("RESULTS_DIR", "results"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.lock = threading.Lock()

    def _log(self, event):
        with self.lock, open(self.dir / f"events-{os.getpid()}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def run_update(self, fields):
        self._log({"run": fields})

    def test_update(self, key, fields):
        self._log({"test": key, **fields})

    def add_step(self, key, step):
        self._log({"test": key, "step": step})

    def upload(self, name, data, content_type):
        (self.dir / name).write_bytes(data)
        return name

    def flush(self):
        pass


class CloudSink:
    """Publishes to demoRuns/{run_id} in Firestore and demo/{run_id}/ in the website's storage bucket."""

    def __init__(self, run_id):
        from google.cloud import firestore, storage

        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "keelward-web-50bc7")
        self.firestore = firestore
        self.doc = firestore.Client(project=project).collection("demoRuns").document(run_id)
        bucket = os.environ.get("DEMO_BUCKET", f"{project}.firebasestorage.app")
        self.bucket = storage.Client(project=project).bucket(bucket)
        self.run_id = run_id
        self.pool = ThreadPoolExecutor(max_workers=1)

    def _submit(self, fn, *args):
        def safe():
            try:
                fn(*args)
            except Exception as e:  # reporting must never break a test run
                print(f"[keelward-live] write failed: {e}")
        self.pool.submit(safe)

    def run_update(self, fields):
        self._submit(self.doc.update, fields)

    def test_update(self, key, fields):
        self._submit(self.doc.update, {f"tests.{key}.{k}": v for k, v in fields.items()})

    def add_step(self, key, step):
        self._submit(self.doc.update, {f"tests.{key}.steps": self.firestore.ArrayUnion([step])})

    def upload(self, name, data, content_type):
        def put():
            blob = self.bucket.blob(f"demo/{self.run_id}/{name}")
            blob.cache_control = "public, max-age=31536000, immutable"
            blob.upload_from_string(data, content_type=content_type)
        self._submit(put)
        return name

    def flush(self):
        self.pool.shutdown(wait=True)
        self.pool = ThreadPoolExecutor(max_workers=1)


def make_sink():
    run_id = os.environ.get("RUN_ID", "")
    if run_id and os.environ.get("LIVE", "1") != "0":
        return CloudSink(run_id)
    return LocalSink(run_id or "local")
