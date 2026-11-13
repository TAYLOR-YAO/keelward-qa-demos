"""Free website check: loads ONE public page the way a visitor's browser would (desktop and phone) and reports on
accessibility, speed, links, security headers, mobile layout and search basics.

    CHECK_ID=... URL=https://example.com python -m sitecheck.check     # writes to Firestore siteChecks/{id}
    URL=https://example.com python -m sitecheck.check                  # local: prints the report as JSON

It never crawls: one page, its own resources, and a light HEAD request to at most 40 of its links.
Every request (the browser's and ours) must go to a public internet address; anything private, local or
internal is blocked, so a submitted page cannot reach the service's own network.
"""
import ipaddress
import json
import os
import socket
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import requests

UA = "Mozilla/5.0 (compatible; KeelwardSiteCheck/1.0; +https://keelward.io/check/)"
MAX_LINKS = 40
PAGE_TIMEOUT = 30_000
SECURITY_HEADERS = ["strict-transport-security", "content-security-policy", "x-content-type-options", "referrer-policy", "permissions-policy"]
UNVERIFIABLE = {401, 403, 405, 429, 999}  # sites that refuse automated requests: not counted as broken


class Blocked(Exception):
    pass


# ---------------------------------------------------------------- network guard
@lru_cache(maxsize=512)
def _public(host):
    """True when every address the host resolves to is a public internet address."""
    host = (host or "").strip("[]").lower()
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        return False
    try:
        ipaddress.ip_address(host)
        return False  # raw IP addresses are not accepted
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return False
    addrs = {i[4][0].split("%")[0] for i in infos}
    return bool(addrs) and all(ipaddress.ip_address(a).is_global for a in addrs)


def allowed(url):
    p = urlparse(url)
    if p.scheme in ("data", "blob", "about"):
        return True
    return p.scheme in ("http", "https") and (p.port in (None, 80, 443)) and _public(p.hostname)


def fetch(url, method="HEAD", hops=5, timeout=8):
    """A request that follows redirects itself, checking every hop is public. Returns (response, final_url)."""
    for _ in range(hops + 1):
        if not allowed(url):
            raise Blocked(url)
        r = requests.request(method, url, allow_redirects=False, timeout=timeout, headers={"User-Agent": UA}, stream=True)
        r.close()
        if r.is_redirect and r.headers.get("location"):
            url = urljoin(url, r.headers["location"])
            continue
        return r, url
    raise requests.TooManyRedirects(url)


# ---------------------------------------------------------------- the checks
def grade(score):
    return "good" if score >= 90 else "fair" if score >= 50 else "poor"


def item(code, status, **data):
    """One line of the report: code (translated on the website), status good|warn|bad|info, and its numbers."""
    return {"code": code, "status": status, **data}


def score_of(items):
    weights = {"good": 1.0, "warn": 0.5, "bad": 0.0}
    scored = [weights[i["status"]] for i in items if i["status"] in weights]
    return round(100 * sum(scored) / len(scored)) if scored else 100


PERF_JS = """async () => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const res = performance.getEntriesByType('resource');
  let lcp = 0, cls = 0;
  await new Promise((done) => {
    try {
      new PerformanceObserver((l) => { for (const e of l.getEntries()) lcp = Math.max(lcp, e.renderTime || e.loadTime || e.startTime); }).observe({ type: 'largest-contentful-paint', buffered: true });
      new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) cls += e.value; }).observe({ type: 'layout-shift', buffered: true });
    } catch (e) {}
    setTimeout(done, 300);
  });
  const fcp = (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime || 0;
  const bytes = (nav.transferSize || 0) + res.reduce((t, r) => t + (r.transferSize || 0), 0);
  const kinds = {};
  for (const r of res) { const k = r.initiatorType || 'other'; kinds[k] = (kinds[k] || 0) + (r.transferSize || 0); }
  return { ttfb: Math.round(nav.responseStart || 0), fcp: Math.round(fcp), lcp: Math.round(lcp), cls: Math.round(cls * 1000) / 1000,
           load: Math.round(nav.loadEventEnd || nav.domContentLoadedEventEnd || 0), requests: res.length + 1, bytes, kinds };
}"""

PAGE_JS = """() => {
  const q = (s) => document.querySelector(s);
  const meta = (n) => (q(`meta[name="${n}"]`) || q(`meta[property="${n}"]`) || {}).content || '';
  const links = [...document.querySelectorAll('a[href]')].map((a) => a.href).filter((h) => /^https?:/i.test(h));
  return {
    title: document.title || '', description: meta('description'), lang: document.documentElement.lang || '',
    h1: [...document.querySelectorAll('h1')].map((h) => h.textContent.trim().slice(0, 120)),
    canonical: (q('link[rel="canonical"]') || {}).href || '', robots: meta('robots'),
    ogTitle: meta('og:title'), ogImage: meta('og:image'), viewport: meta('viewport'),
    favicon: !!q('link[rel~="icon"]'), images: document.images.length,
    links: [...new Set(links.map((h) => h.split('#')[0]))],
  };
}"""

MOBILE_JS = """() => {
  const W = document.documentElement.clientWidth;
  const overflow = document.documentElement.scrollWidth - W;
  let small = 0, tiny = 0, examples = [];
  for (const el of document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, [role=button]')) {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none') continue;
    if (r.width < 24 || r.height < 24) { small++; if (examples.length < 3) examples.push((el.innerText || el.getAttribute('aria-label') || el.tagName).trim().slice(0, 40)); }
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const p = walker.currentNode.parentElement;
    if (!p || seen.has(p) || !walker.currentNode.textContent.trim()) continue;
    seen.add(p);
    const fs = parseFloat(getComputedStyle(p).fontSize);
    const r = p.getBoundingClientRect();
    if (fs < 12 && r.width && r.height) tiny++;
  }
  return { width: W, overflow: Math.max(0, overflow), smallTargets: small, smallExamples: examples, tinyText: tiny };
}"""


def run(url, progress=lambda step: None):
    from axe_playwright_python.sync_playwright import Axe
    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    if not allowed(url):
        raise Blocked(url)
    report = {"url": url, "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sections": {}, "shots": {}}
    blocked, mixed, errors, failed = set(), set(), [], []

    def guard(route):
        u = route.request.url
        if allowed(u):
            return route.continue_()
        blocked.add(urlparse(u).hostname or u[:60])
        return route.abort("blockedbyclient")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--disable-dev-shm-usage"])
        try:
            # ------------------------------------------------ desktop: load, speed, accessibility, content
            progress("load")
            ctx = browser.new_context(viewport={"width": 1366, "height": 900}, user_agent=UA + " Chrome", ignore_https_errors=False,
                                      service_workers="block", accept_downloads=False)
            ctx.route("**/*", guard)
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)[:200]))
            # Failed downloads are counted separately below, so only script errors count here.
            page.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource") and errors.append(m.text[:200]))
            page.on("requestfailed", lambda r: "BLOCKED_BY_CLIENT" not in (r.failure or "") and failed.append(r.url[:200]))
            page.on("response", lambda r: r.status >= 400 and r.request.resource_type != "document" and failed.append(f"{r.status} {r.url[:180]}"))
            main = {}

            def on_response(resp):
                if resp.request.is_navigation_request() and resp.frame == page.main_frame:
                    main.update(status=resp.status, url=resp.url, headers=resp.all_headers())
                if resp.url.startswith("http://") and page.url.startswith("https://"):
                    mixed.add(resp.url[:200])
            page.on("response", on_response)
            t0 = time.monotonic()
            try:
                page.goto(url, wait_until="load", timeout=PAGE_TIMEOUT)
            except Exception as e:
                if not main:
                    raise RuntimeError(f"load: {str(e).splitlines()[0][:200]}")
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            report["loadSeconds"] = round(time.monotonic() - t0, 1)
            final = page.url
            report["finalUrl"] = final
            report["status"] = main.get("status")
            report["title"] = page.title()[:160]
            if (main.get("status") or 0) >= 400:
                raise RuntimeError(f"status:{main.get('status')}")
            report["shots"]["desktop"] = page.screenshot(type="jpeg", quality=70)

            progress("speed")
            perf = page.evaluate(PERF_JS)
            report["sections"]["speed"] = speed(perf, errors, failed)

            progress("content")
            info = page.evaluate(PAGE_JS)
            report["sections"]["seo"] = seo(info)

            progress("accessibility")
            try:
                axe = Axe().run(page, options={"runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]},
                                               "resultTypes": ["violations", "passes"]}).response
                report["sections"]["accessibility"] = accessibility(axe)
            except Exception as e:
                report["sections"]["accessibility"] = {"score": None, "items": [item("a11y-unavailable", "info", detail=type(e).__name__)]}
            ctx.close()

            # ------------------------------------------------ phone
            progress("mobile")
            device = dict(pw.devices["iPhone 13"])
            device.pop("default_browser_type", None)
            mctx = browser.new_context(**device, service_workers="block")
            mctx.route("**/*", guard)
            mpage = mctx.new_page()
            try:
                mpage.goto(final, wait_until="load", timeout=PAGE_TIMEOUT)
                try:
                    mpage.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                m = mpage.evaluate(MOBILE_JS)
                report["shots"]["mobile"] = mpage.screenshot(type="jpeg", quality=70)
                report["sections"]["mobile"] = mobile(m, info)
            except Exception as e:
                report["sections"]["mobile"] = {"score": None, "items": [item("mobile-unavailable", "info", detail=str(e).splitlines()[0][:120])]}
            mctx.close()
        finally:
            browser.close()

    # ---------------------------------------------------- security and links (plain HTTP requests)
    progress("security")
    report["sections"]["security"] = security(final, main.get("headers", {}), mixed)
    progress("links")
    report["sections"]["links"] = links(info["links"], final)

    for s in report["sections"].values():
        if s.get("score") is None and any(i["status"] in ("good", "warn", "bad") for i in s["items"]):
            s["score"] = score_of(s["items"])
        s["grade"] = grade(s["score"]) if s.get("score") is not None else None
    scored = [s["score"] for s in report["sections"].values() if s.get("score") is not None]
    report["score"] = round(sum(scored) / len(scored)) if scored else None
    report["grade"] = grade(report["score"]) if report["score"] is not None else None
    report["blockedHosts"] = sorted(blocked)[:10]
    report["seconds"] = round(time.monotonic() - started, 1)
    return report


def speed(p, errors, failed):
    mb = p["bytes"] / 1_000_000
    items = [
        item("lcp", "good" if p["lcp"] and p["lcp"] <= 2500 else "warn" if p["lcp"] and p["lcp"] <= 4000 else "bad" if p["lcp"] else "info", value=p["lcp"]),
        item("fcp", "good" if p["fcp"] <= 1800 else "warn" if p["fcp"] <= 3000 else "bad", value=p["fcp"]),
        item("ttfb", "good" if p["ttfb"] <= 800 else "warn" if p["ttfb"] <= 1800 else "bad", value=p["ttfb"]),
        item("cls", "good" if p["cls"] <= 0.1 else "warn" if p["cls"] <= 0.25 else "bad", value=p["cls"]),
        item("weight", "good" if mb <= 2 else "warn" if mb <= 5 else "bad", value=round(mb, 2),
             biggest=sorted(((k, round(v / 1_000_000, 2)) for k, v in p["kinds"].items()), key=lambda x: -x[1])[:3]),
        item("requests", "good" if p["requests"] <= 80 else "warn" if p["requests"] <= 150 else "bad", value=p["requests"]),
        item("js-errors", "good" if not errors else "warn" if len(errors) <= 2 else "bad", value=len(errors), examples=errors[:3]),
        item("failed-requests", "good" if not failed else "warn" if len(failed) <= 3 else "bad", value=len(failed), examples=failed[:3]),
    ]
    return {"items": items, "metrics": {k: p[k] for k in ("ttfb", "fcp", "lcp", "cls", "load", "requests", "bytes")}}


def accessibility(r):
    impact_rank = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
    violations = sorted(r.get("violations", []), key=lambda v: (impact_rank.get(v.get("impact"), 4), -len(v.get("nodes", []))))
    issues = [{"id": v["id"], "impact": v.get("impact") or "minor", "help": v.get("help", "")[:160], "url": v.get("helpUrl", ""),
               "count": len(v.get("nodes", [])),
               "examples": [{"target": " ".join(map(str, n.get("target", [])))[:120], "html": n.get("html", "")[:180]} for n in v.get("nodes", [])[:2]]}
              for v in violations]
    passes = len(r.get("passes", []))
    # Weighted like the impact it has on people: one critical issue costs more than several minor ones.
    penalty = sum({"critical": 10, "serious": 7, "moderate": 3, "minor": 1}.get(i["impact"], 1) for i in issues)
    score = max(0, round(100 - penalty * 100 / max(25, passes + penalty)))
    counts = {k: sum(1 for i in issues if i["impact"] == k) for k in ("critical", "serious", "moderate", "minor")}
    return {"score": score, "issues": issues[:15], "counts": counts, "passes": passes,
            "items": [item("a11y-summary", "good" if not issues else "warn" if not (counts["critical"] or counts["serious"]) else "bad", **counts, passes=passes)]}


def seo(i):
    t, d = len(i["title"]), len(i["description"])
    items = [
        item("title", "good" if 10 <= t <= 65 else "warn" if t else "bad", value=i["title"][:120], length=t),
        item("description", "good" if 50 <= d <= 170 else "warn" if d else "bad", value=i["description"][:200], length=d),
        item("lang", "good" if i["lang"] else "bad", value=i["lang"]),
        item("h1", "good" if len(i["h1"]) == 1 else "warn" if i["h1"] else "bad", value=len(i["h1"]), examples=i["h1"][:3]),
        item("canonical", "good" if i["canonical"] else "warn", value=i["canonical"][:200]),
        item("social", "good" if i["ogTitle"] and i["ogImage"] else "warn"),
        item("favicon", "good" if i["favicon"] else "warn"),
    ]
    if "noindex" in i["robots"].lower():
        items.append(item("noindex", "warn", value=i["robots"][:80]))
    return {"items": items}


def mobile(m, info):
    vp = info.get("viewport", "")
    items = [
        item("viewport", "good" if "width=device-width" in vp.replace(" ", "") else "bad", value=vp[:120]),
        item("overflow", "good" if m["overflow"] <= 2 else "bad", value=m["overflow"], width=m["width"]),
        item("tap-targets", "good" if m["smallTargets"] == 0 else "warn" if m["smallTargets"] <= 5 else "bad", value=m["smallTargets"], examples=m["smallExamples"]),
        item("text-size", "good" if m["tinyText"] == 0 else "warn" if m["tinyText"] <= 5 else "bad", value=m["tinyText"]),
    ]
    return {"items": items}


def security(final, headers, mixed):
    h = {k.lower(): v for k, v in (headers or {}).items()}
    https = final.startswith("https://")
    items = [item("https", "good" if https else "bad")]
    if https:
        try:
            r, landed = fetch("http://" + final.split("://", 1)[1].split("/", 1)[0] + "/", timeout=6)
            items.append(item("http-redirect", "good" if landed.startswith("https://") else "bad", value=landed[:120]))
        except Exception:
            items.append(item("http-redirect", "info"))
    for name in SECURITY_HEADERS:
        present = name in h
        if name == "strict-transport-security" and not https:
            continue
        sev = "bad" if name in ("strict-transport-security", "x-content-type-options") else "warn"
        items.append(item(f"hdr-{name}", "good" if present else sev, value=(h.get(name) or "")[:160]))
    framing = "x-frame-options" in h or "frame-ancestors" in h.get("content-security-policy", "")
    items.append(item("hdr-framing", "good" if framing else "warn"))
    items.append(item("mixed-content", "good" if not mixed else "bad", value=len(mixed), examples=sorted(mixed)[:3]))
    server = h.get("server", "") + " " + h.get("x-powered-by", "")
    if any(ch.isdigit() for ch in server):
        items.append(item("version-leak", "warn", value=server.strip()[:80]))
    return {"items": items}


def links(urls, base):
    urls = [u for u in urls if u.split("#")[0].rstrip("/") != base.split("#")[0].rstrip("/")][:MAX_LINKS]

    def one(u):
        try:
            r, _ = fetch(u, "HEAD")
            if r.status_code in (403, 404, 405, 501):  # some servers answer HEAD badly: confirm with GET
                r, _ = fetch(u, "GET")
            code = r.status_code
            return {"url": u, "status": code, "state": "ok" if code < 400 else "unverified" if code in UNVERIFIABLE else "broken"}
        except Blocked:
            return {"url": u, "status": None, "state": "skipped"}
        except requests.RequestException as e:
            return {"url": u, "status": None, "state": "broken", "error": type(e).__name__}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, urls))
    broken = [r for r in results if r["state"] == "broken"]
    checked = sum(1 for r in results if r["state"] in ("ok", "broken"))
    items = [item("links", "good" if not broken else "warn" if len(broken) <= 2 else "bad", checked=checked, broken=len(broken),
                  unverified=sum(1 for r in results if r["state"] == "unverified"))]
    return {"items": items, "broken": broken[:15]}


# ---------------------------------------------------------------- publishing
def main():
    url = os.environ["URL"]
    check_id = os.environ.get("CHECK_ID")
    if not check_id:
        r = run(url, progress=lambda s: print(f"[check] {s}", file=sys.stderr))
        r["shots"] = {k: f"{len(v)} bytes" for k, v in r["shots"].items()}
        print(json.dumps(r, indent=1, default=str))
        return

    from google.cloud import firestore, storage

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "keelward-web-50bc7")
    doc = firestore.Client(project=project).collection("siteChecks").document(check_id)
    bucket = storage.Client(project=project).bucket(os.environ.get("DEMO_BUCKET", f"{project}.firebasestorage.app"))
    doc.update({"status": "running", "step": "load", "startedAt": firestore.SERVER_TIMESTAMP})
    try:
        report = run(url, progress=lambda s: doc.update({"step": s}))
        for name, data in report.pop("shots").items():
            blob = bucket.blob(f"check/{check_id}/{name}.jpg")
            blob.cache_control = "private, max-age=3600"
            blob.upload_from_string(data, content_type="image/jpeg")
            report.setdefault("files", []).append(f"{name}.jpg")
        doc.update({"status": "done", "step": "done", "report": report, "score": report["score"], "finishedAt": firestore.SERVER_TIMESTAMP})
    except Blocked:
        doc.update({"status": "failed", "error": "blocked", "finishedAt": firestore.SERVER_TIMESTAMP})
    except Exception as e:
        msg = str(e)
        code = "status" if msg.startswith("status:") else "load" if msg.startswith("load:") else "error"
        print(f"[check] {check_id} failed: {type(e).__name__}: {msg[:300]}\n{traceback.format_exc()[-1500:]}")
        doc.update({"status": "failed", "error": code, "errorDetail": msg[:200], "finishedAt": firestore.SERVER_TIMESTAMP})


if __name__ == "__main__":
    main()
