"""AI demo: image and video AI, judged against facts the test itself records.

The inputs are made live: screenshots of a public practice store (healthy and broken accounts), a screen
recording of a checkout that fails, and drawn listing photos. RELEASE=good / regressed picks the AI release.
"""
import os
import tempfile
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

RELEASE = os.environ.get("RELEASE") if os.environ.get("RELEASE") in ("good", "regressed") else "good"
STORE = os.environ.get("SHOP_URL", "https://www.saucedemo.com").rstrip("/")
UA = "KeelwardQADemo/1.0 (+https://keelward.io/demo/) Playwright"


@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p


def _sign_in(page, user):
    page.goto(STORE + "/")
    page.fill('[data-test="username"]', user)
    page.fill('[data-test="password"]', "secret_sauce")  # published on the practice site
    page.click('[data-test="login-button"]')
    page.locator('[data-test="inventory-list"]').wait_for()


@pytest.fixture(scope="session")
def catalog_shots(pw):
    """Catalog screenshots: the store's normal account and its built-in "problem" account (broken photos)."""
    browser = pw.chromium.launch()
    shots = {}
    for user in ("standard_user", "problem_user"):
        ctx = browser.new_context(viewport={"width": 1100, "height": 900}, user_agent=UA)
        page = ctx.new_page()
        _sign_in(page, user)
        page.wait_for_load_state("networkidle")
        shots[user] = page.screenshot(type="png", full_page=True)
        ctx.close()
    browser.close()
    return shots


@pytest.fixture(scope="session")
def checkout_video(pw):
    """A screen recording of a checkout that fails, with the time of each event measured by the test itself."""
    browser = pw.chromium.launch()
    folder = tempfile.mkdtemp(prefix="vid-")
    ctx = browser.new_context(viewport={"width": 800, "height": 600}, record_video_dir=folder,
                              record_video_size={"width": 800, "height": 600}, user_agent=UA)
    t0 = time.monotonic()
    page = ctx.new_page()
    at = {}
    page.wait_for_timeout(600)
    _sign_in(page, "problem_user")
    at["signed-in"] = time.monotonic() - t0
    page.wait_for_timeout(1200)
    page.click('[data-test="add-to-cart-sauce-labs-backpack"]')
    at["added-to-cart"] = time.monotonic() - t0
    page.wait_for_timeout(1200)
    page.click('[data-test="shopping-cart-link"]')
    page.wait_for_timeout(800)
    page.click('[data-test="checkout"]')
    page.locator('[data-test="firstName"]').wait_for()
    at["opened-checkout"] = time.monotonic() - t0
    page.wait_for_timeout(1000)
    page.fill('[data-test="firstName"]', "Ada")
    page.fill('[data-test="lastName"]', "Lovelace")
    page.fill('[data-test="postalCode"]', "30303")
    page.click('[data-test="continue"]')
    page.locator('[data-test="error"]').wait_for()
    at["error-shown"] = time.monotonic() - t0
    error = page.locator('[data-test="error"]').inner_text()
    page.wait_for_timeout(2500)
    video = page.video
    ctx.close()
    browser.close()
    return {"webm": Path(video.path()).read_bytes(), "at": {k: round(v, 1) for k, v in at.items()}, "error": error}
