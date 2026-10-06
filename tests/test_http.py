import httpx
import pytest

from app.core.http import BlockedError, FetchError, PoliteClient, looks_blocked
from tests.conftest import fast_client


def test_cloudflare_page_is_blocked():
    body = "<title>Attention Required! | Cloudflare</title>"
    assert looks_blocked(403, body)
    assert looks_blocked(200, "<script>window._pxAppId = 'x';</script>")
    assert not looks_blocked(200, "<html>ok</html>")


def test_blocked_raises_and_does_not_retry():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(403, text="Access to this page has been denied")

    with pytest.raises(BlockedError):
        fast_client(handler).get_text("https://example.com/x")
    assert len(calls) == 1


def test_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.core.http.time.sleep", lambda s: None)
    seq = iter([httpx.Response(503), httpx.Response(200, text="ok")])
    assert fast_client(lambda r: next(seq)).get_text("https://example.com/") == "ok"


def test_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr("app.core.http.time.sleep", lambda s: None)
    with pytest.raises(FetchError):
        fast_client(lambda r: httpx.Response(500)).get_text("https://example.com/")


def test_cache_avoids_second_request(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, text="cached!")

    c = PoliteClient(min_delay=0, max_delay=0, cache_ttl=60, cache_dir=tmp_path,
                     transport=httpx.MockTransport(handler))
    assert c.get_text("https://example.com/a") == "cached!"
    assert c.get_text("https://example.com/a") == "cached!"
    assert len(calls) == 1


def test_rate_limit_waits(monkeypatch):
    sleeps = []
    monkeypatch.setattr("app.core.http.time.sleep", lambda s: sleeps.append(s))
    PoliteClient._last_hit.clear()
    c = PoliteClient(min_delay=3, max_delay=5, cache_ttl=0,
                     transport=httpx.MockTransport(lambda r: httpx.Response(200, text="x")))
    c.get_text("https://rate.example/1")
    c.get_text("https://rate.example/2")
    assert sleeps and 2.5 < sleeps[-1] <= 5
