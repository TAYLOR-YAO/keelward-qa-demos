"""Harbor & Co shop API: the application under test in the performance demo. It runs next to the load test,
so the demo never puts load on anyone else's site.

  GET  /api/products?q=...    search the catalog
  GET  /api/products/{id}     one product
  POST /api/cart              add to cart (writes)

Every database query takes about 12 ms. Two releases:
  good       a pool of 10 database connections and a cache for product pages
  regressed  "simplified configuration": one shared database connection and no cache. Fine for one user;
             under load, requests queue for the connection.

    RELEASE=regressed PORT=8091 python -m apps.shop_api
"""
import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

QUERY_SECONDS = 0.012
PRODUCTS = {f"P{100 + i}": {"id": f"P{100 + i}", "name": f"{c} item {i % 12 + 1}", "category": c, "price": round(8 + (i * 7.3) % 170, 2)}
            for i, c in enumerate(["Kitchen", "Outdoor", "Office", "Audio"] * 12)}


class Database:
    def __init__(self, connections):
        self.pool = threading.BoundedSemaphore(connections)

    def query(self, cost=1.0):
        with self.pool:  # wait for a free connection
            time.sleep(QUERY_SECONDS * cost * random.uniform(0.85, 1.15))


def make_handler(release):
    db = Database(10 if release == "good" else 1)
    cache = {}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/health":
                return self._send(200, {"ok": True, "release": release})
            if u.path == "/api/products":
                q = (parse_qs(u.query).get("q") or [""])[0].lower()
                db.query(1.5)  # a search reads more rows
                return self._send(200, [p for p in PRODUCTS.values() if q in p["name"].lower() or q in p["category"].lower()][:20])
            if u.path.startswith("/api/products/"):
                pid = u.path.rsplit("/", 1)[1]
                if pid not in PRODUCTS:
                    return self._send(404, {"error": "not found"})
                if release == "good" and pid in cache:
                    return self._send(200, cache[pid])
                db.query()
                if release == "good":
                    with lock:
                        cache[pid] = PRODUCTS[pid]
                return self._send(200, PRODUCTS[pid])
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if urlparse(self.path).path != "/api/cart":
                return self._send(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(min(n, 2000)) or b"{}")
            db.query()  # read stock
            db.query()  # write cart line
            return self._send(201, {"ok": True, "product": body.get("product")})

        def log_message(self, *a):
            pass

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 512


if __name__ == "__main__":
    release = os.environ.get("RELEASE", "good")
    port = int(os.environ.get("PORT", "8091"))
    Server(("127.0.0.1", port), make_handler(release)).serve_forever()
