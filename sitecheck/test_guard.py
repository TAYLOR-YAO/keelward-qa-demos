"""The network guard of the website check: only public internet addresses may be reached."""
import socket

import pytest

from sitecheck import check


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    table = {"example.com": ["93.184.215.14"], "rebind.example": ["93.184.215.14", "10.0.0.5"], "meta.example": ["169.254.169.254"],
             "v6.example": ["2606:4700::6810:84e5"], "loop6.example": ["::1"]}

    def gai(host, *a, **k):
        if host not in table:
            raise socket.gaierror("not found")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in table[host]]
    monkeypatch.setattr(socket, "getaddrinfo", gai)
    check._public.cache_clear()


@pytest.mark.parametrize("url", ["https://example.com/", "http://example.com/a?b=1", "https://v6.example/", "data:text/plain,x"])
def test_public_addresses_are_allowed(url):
    assert check.allowed(url)


@pytest.mark.parametrize("url", [
    "http://localhost:8080/run", "http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/computeMetadata/v1/",
    "http://metadata.google.internal/", "https://meta.example/", "https://rebind.example/", "https://loop6.example/",
    "https://example.com:8080/", "ftp://example.com/", "file:///etc/passwd", "https://unknown.example/", "http://10.1.2.3/",
    "http://printer.local/", "javascript:alert(1)",
])
def test_private_local_and_odd_addresses_are_blocked(url):
    assert not check.allowed(url)


def test_redirects_to_private_addresses_are_blocked(monkeypatch):
    class R:
        is_redirect, status_code, headers = True, 302, {"location": "http://169.254.169.254/"}

        def close(self):
            pass
    monkeypatch.setattr(check.requests, "request", lambda *a, **k: R())
    with pytest.raises(check.Blocked):
        check.fetch("https://example.com/")
