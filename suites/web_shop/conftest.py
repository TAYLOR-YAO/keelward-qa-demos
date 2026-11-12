"""Web shop regression suite: Sauce Demo (https://www.saucedemo.com), a public practice store.

RELEASE=stable signs in as the store's normal account. RELEASE=regression signs in as its built-in
"problem" account, which behaves like a release that shipped with defects. The tests are identical;
only the build under test changes.
"""
import tempfile
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from shop import Store


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--disable-dev-shm-usage"])
        yield b
        b.close()


@pytest.fixture
def page(browser, live, step):
    sink, key, writer = live
    video_dir = Path(tempfile.mkdtemp(prefix="video-"))
    context = browser.new_context(
        viewport={"width": 1000, "height": 680},
        record_video_dir=str(video_dir) if writer else None,
        record_video_size={"width": 800, "height": 544},
        user_agent="KeelwardQADemo/1.0 (+https://keelward.io/demo/) Playwright",
    )
    context.set_default_timeout(8000)
    pg = context.new_page()
    step.page = pg
    yield pg
    video = pg.video
    context.close()
    if writer and video:
        try:
            data = Path(video.path()).read_bytes()
            sink.test_update(key, {"video": sink.upload(f"{key}.webm", data, "video/webm")})
        except Exception as e:
            print(f"[keelward-live] video not saved: {e}")


@pytest.fixture
def store(page, step):
    return Store(page, step)
