"""Settings and a small client for Restful-Booker (https://restful-booker.herokuapp.com), a public practice API."""
import os
import time

import requests

BASE_URL = os.environ.get("BOOKER_URL", "https://restful-booker.herokuapp.com").rstrip("/")
ADMIN = {"username": "admin", "password": "password123"}  # published in the practice API's documentation


def new_booking(**over):
    data = {
        "firstname": "Keelward",
        "lastname": f"Demo{int(time.time() * 1000) % 10_000_000}",
        "totalprice": 240,
        "depositpaid": True,
        "bookingdates": {"checkin": "2027-03-10", "checkout": "2027-03-14"},
        "additionalneeds": "Breakfast",
    }
    data.update(over)
    return data


class Api:
    """requests session that records each call (method, path, status, time) on the current test step."""

    def __init__(self, step):
        self.step = step
        self.http = requests.Session()
        self.http.headers.update({"Accept": "application/json", "Content-Type": "application/json",
                                  "User-Agent": "KeelwardQADemo/1.0 (+https://keelward.io/demo/)"})
        self.token = None

    def call(self, method, path, auth=False, **kw):
        headers = kw.pop("headers", {})
        if auth and self.token:
            headers["Cookie"] = f"token={self.token}"
        start = time.monotonic()
        r = self.http.request(method, BASE_URL + path, headers=headers, timeout=15, **kw)
        r.elapsed_ms = int((time.monotonic() - start) * 1000)
        self.step.detail(f"{method} {path} → {r.status_code} · {r.elapsed_ms} ms")
        return r

    def login(self):
        r = self.call("POST", "/auth", json=ADMIN)
        self.token = r.json().get("token")
        return r

    def create(self, **over):
        return self.call("POST", "/booking", json=new_booking(**over))
