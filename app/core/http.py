"""Polite HTTP client shared by every adapter.

* one request per host every `min..max` seconds (random jitter)
* exponential backoff on network errors / 5xx / 429
* on-disk response cache (TTL) so retries and re-runs don't hit the site again
* anti-bot pages (Cloudflare, PerimeterX, captcha) raise BlockedError: we stop, never bypass
"""

import hashlib
import json
import logging
import random
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

BLOCK_MARKERS = (
    "attention required! | cloudflare",
    "cf-chl-",
    "challenge-platform",
    "access to this page has been denied",
    "_pxappid",
    "px-captcha",
    "g-recaptcha",
    "hcaptcha",
    "are you a robot",
    "captcha-delivery",
)


class BlockedError(RuntimeError):
    """The site answered with an anti-bot block or captcha."""


class FetchError(RuntimeError):
    pass


def looks_blocked(status: int, body: str) -> bool:
    lowered = body[:20000].lower()
    if any(m in lowered for m in BLOCK_MARKERS):
        return True
    return status in (401, 403) or (status == 429 and "captcha" in lowered)


class PoliteClient:
    _host_lock = threading.Lock()
    _last_hit: dict[str, float] = {}

    def __init__(
        self,
        *,
        min_delay: float | None = None,
        max_delay: float | None = None,
        cache_ttl: int | None = None,
        cache_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        s = get_settings()
        self.min_delay = s.http_min_delay if min_delay is None else min_delay
        self.max_delay = s.http_max_delay if max_delay is None else max_delay
        self.cache_ttl = s.http_cache_ttl if cache_ttl is None else cache_ttl
        self.cache_dir = cache_dir if cache_dir is not None else s.cache_dir
        self.max_retries = s.http_max_retries
        base_headers = {
            "User-Agent": s.user_agent,
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        }
        base_headers.update(headers or {})
        self._client = httpx.Client(
            headers=base_headers,
            timeout=s.http_timeout,
            follow_redirects=True,
            transport=transport,
        )

    # -- rate limiting -------------------------------------------------------
    def _wait_turn(self, host: str) -> None:
        if self.max_delay <= 0:
            return
        with self._host_lock:
            last = self._last_hit.get(host, 0.0)
            gap = random.uniform(self.min_delay, self.max_delay)
            wait = last + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.monotonic()

    # -- cache ---------------------------------------------------------------
    def _cache_path(self, method: str, url: str, params: dict | None, body: object) -> Path:
        key = json.dumps([method, url, params, body], sort_keys=True, default=str)
        return self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def _cache_get(self, path: Path) -> str | None:
        if self.cache_ttl <= 0 or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl:
            return None
        return json.loads(path.read_text())["text"]

    def _cache_put(self, path: Path, text: str) -> None:
        if self.cache_ttl <= 0:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"text": text}))
        except OSError:
            log.warning("http cache write failed", extra={"path": str(path)})

    # -- request -------------------------------------------------------------
    def get_text(self, url: str, *, params: dict | None = None, use_cache: bool = True) -> str:
        return self.request("GET", url, params=params, use_cache=use_cache)

    def get_json(self, url: str, *, params: dict | None = None, use_cache: bool = True):
        return json.loads(self.request("GET", url, params=params, use_cache=use_cache))

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: object = None,
        use_cache: bool = True,
        headers: dict[str, str] | None = None,
    ) -> str:
        cpath = self._cache_path(method, url, params, json_body)
        if use_cache and (cached := self._cache_get(cpath)) is not None:
            return cached

        host = urlparse(url).netloc
        attempt = 0
        while True:
            attempt += 1
            self._wait_turn(host)
            try:
                resp = self._client.request(
                    method, url, params=params, json=json_body, headers=headers
                )
            except httpx.TransportError as exc:
                if attempt > self.max_retries:
                    raise FetchError(f"{url}: {exc}") from exc
                self._backoff(attempt, url, str(exc))
                continue

            text = resp.text
            if looks_blocked(resp.status_code, text):
                log.warning("blocked by anti-bot", extra={"url": url, "status": resp.status_code})
                raise BlockedError(f"{host} respondeu {resp.status_code} (anti-bot/captcha)")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt > self.max_retries:
                    raise FetchError(f"{url}: HTTP {resp.status_code}")
                self._backoff(attempt, url, f"HTTP {resp.status_code}")
                continue
            if resp.status_code >= 400:
                raise FetchError(f"{url}: HTTP {resp.status_code}")
            if use_cache:
                self._cache_put(cpath, text)
            return text

    def _backoff(self, attempt: int, url: str, reason: str) -> None:
        delay = min(60.0, (2**attempt) + random.uniform(0, 1))
        log.info("retrying", extra={"url": url, "attempt": attempt, "reason": reason, "sleep": delay})
        time.sleep(delay)

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
