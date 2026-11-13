"""Load profile for the performance demo: shoppers who search, open products and add to cart.

Stages (users): 10 for 10 s (normal day), 50 for 12 s (expected peak), 100 for 12 s (sale day),
back to 10 for 10 s (recovery). Every request must answer within 2 seconds.
"""
import random
import sys
from pathlib import Path

from locust import HttpUser, LoadTestShape, between, task

sys.path.insert(0, str(Path(__file__).parent))
from stages import STAGES  # noqa: E402

PIDS = [f"P{100 + i}" for i in range(48)]
WORDS = ["kitchen", "outdoor", "office", "audio", "item 3", "item 7"]


class Shopper(HttpUser):
    wait_time = between(0.3, 0.8)

    @task(5)
    def view_product(self):
        self.client.get(f"/api/products/{random.choice(PIDS)}", name="Product page", timeout=2)

    @task(3)
    def search(self):
        self.client.get(f"/api/products?q={random.choice(WORDS)}", name="Search", timeout=2)

    @task(1)
    def add_to_cart(self):
        self.client.post("/api/cart", json={"product": random.choice(PIDS), "qty": 1}, name="Add to cart", timeout=2)


class Stages(LoadTestShape):
    def tick(self):
        t = self.get_run_time()
        for end, users, _ in STAGES:
            if t < end:
                return users, 100
        return None


# Every request is logged (start time in the run, endpoint, response time, success) for the tests to analyse.
import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

from locust import events  # noqa: E402

LOG, START = [], [0.0]


@events.test_start.add_listener
def _start(**kw):
    START[0] = time.time()


@events.request.add_listener
def _record(request_type, name, response_time, exception, **kw):
    LOG.append((round(time.time() - START[0] - response_time / 1000, 3), name, round(response_time, 1), exception is None))


@events.quitting.add_listener
def _dump(**kw):
    out = os.environ.get("KL_PERF_LOG")
    if out:
        with open(out, "w") as f:
            json.dump(LOG, f)
