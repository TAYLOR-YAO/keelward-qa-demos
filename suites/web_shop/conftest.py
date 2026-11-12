"""Web shop regression suite: Sauce Demo (https://www.saucedemo.com), a public practice store.

RELEASE=stable signs in as the store's normal account. RELEASE=regression signs in as its built-in
"problem" account, which behaves like a release that shipped with defects. The tests are identical;
only the build under test changes.

BROWSERS=chromium,firefox,webkit,mobile runs every test in each browser (the deeper demo); without it,
tests run once in Chrome (Chromium).
"""
import os
import tempfile
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from shop import Store

BADGE = """
(() => {
  const add = () => {
    if (!document.body || document.getElementById('kw-live-badge')) return;
    const b = document.createElement('div');
    b.id = 'kw-live-badge';
    b.setAttribute('aria-hidden', 'true');
    b.style.cssText = 'position:fixed;right:8px;bottom:8px;z-index:2147483647;pointer-events:none;background:rgba(15,39,66,.92);color:#fff;font:600 11px/1.3 system-ui,sans-serif;padding:5px 9px;border-radius:7px;box-shadow:0 2px 8px rgba(0,0,0,.25)';
    const tick = () => { b.textContent = 'LIVE TEST RUN · ' + new Date().toISOString().replace('T', ' ').slice(0, 19) + ' UTC · ' + location.host + location.pathname + ' · run %s'; };
    tick(); setInterval(tick, 500);
    document.body.appendChild(b);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""


BROWSERS = [b for b in os.environ.get("BROWSERS", "").split(",") if b in ("chromium", "firefox", "webkit", "mobile")]


def pytest_generate_tests(metafunc):
    if BROWSERS and "browser" in metafunc.fixturenames:
        metafunc.parametrize("browser", BROWSERS, indirect=True, scope="session", ids=BROWSERS)


def pytest_collection_modifyitems(config, items):
    """Each browser runs on its own worker; checks marked one_browser run in the first browser only."""
    keep, drop = [], []
    for item in items:
        b = getattr(getattr(item, "callspec", None), "params", {}).get("browser")
        if b and item.get_closest_marker("one_browser") and b != BROWSERS[0]:
            drop.append(item)
            continue
        if b:
            item.add_marker(pytest.mark.xdist_group(b))
        keep.append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(request, pw):
    name = getattr(request, "param", "chromium")
    engine = {"firefox": pw.firefox, "webkit": pw.webkit}.get(name, pw.chromium)
    b = engine.launch(args=["--disable-dev-shm-usage"] if engine is pw.chromium else [])
    yield b
    b.close()


@pytest.fixture
def page(request, browser, pw, live, step):
    sink, key, writer = live
    video_dir = Path(tempfile.mkdtemp(prefix="video-"))
    if getattr(getattr(request.node, "callspec", None), "params", {}).get("browser") == "mobile":
        device = dict(pw.devices["Pixel 7"])
        options = {**device, "user_agent": device["user_agent"] + " KeelwardQADemo/1.0",
                   "record_video_size": {"width": 412, "height": 839}}
    else:
        options = {"viewport": {"width": 1000, "height": 680}, "record_video_size": {"width": 800, "height": 544},
                   "user_agent": "KeelwardQADemo/1.0 (+https://keelward.io/demo/) Playwright"}
    context = browser.new_context(record_video_dir=str(video_dir) if writer else None, **options)
    context.set_default_timeout(8000)
    # A small badge with the live UTC time and address, so the video replay shows when and where it ran.
    # It ignores the mouse and sits in a corner, so it never affects the tests.
    context.add_init_script(BADGE % (os.environ.get("RUN_ID") or "local")[:8])
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
