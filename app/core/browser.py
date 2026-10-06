"""Headless Chromium for sources that only serve real browsers (OLX).

Same approach as findhome's BrowserPool: Chromium is launched lazily (an idle app
stays small), one context is shared so cookies set on the first page are reused,
images/fonts/media are not downloaded, and every navigation goes through the same
per-host rate limit as the HTTP client. A challenge/captcha page raises
BlockedError: we stop and record it, never try to solve it.
"""

from __future__ import annotations

import contextlib
import logging
import random
import threading
import time
from urllib.parse import urlparse

from app.core.config import get_settings
from app.core.http import BlockedError, FetchError, PoliteClient, looks_blocked

log = logging.getLogger(__name__)

CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--mute-audio",
]
HEAVY = {"image", "font", "media"}


class BrowserUnavailable(RuntimeError):
    """Playwright/Chromium is not installed in this environment."""


class BrowserFetcher:
    _lock = threading.Lock()

    def __init__(self, limiter: PoliteClient | None = None) -> None:
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self.limiter = limiter or PoliteClient()

    def _ensure(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on the image
            raise BrowserUnavailable("playwright não instalado (pip install 'carwatch[browser]')") from exc
        s = get_settings()
        self._pw = sync_playwright().start()
        launch: dict = {"headless": True, "args": CHROMIUM_ARGS}
        if s.browser_executable:
            launch["executable_path"] = s.browser_executable
        if s.browser_proxy:
            from urllib.parse import urlparse as _u

            px = _u(s.browser_proxy)
            launch["proxy"] = {"server": f"{px.scheme}://{px.hostname}:{px.port}"}
            if px.username:
                launch["proxy"].update(username=px.username, password=px.password or "")
        try:
            self._browser = self._pw.chromium.launch(**launch)
        except Exception as exc:
            self.close()
            raise BrowserUnavailable(f"chromium não pôde iniciar: {exc}") from exc
        # Use the browser's own version in the UA: a UA that disagrees with the version
        # Chromium reports in its client hints is an inconsistency bot walls look for.
        version = self._browser.version.split(".")[0]
        user_agent = (f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      f"Chrome/{version}.0.0.0 Safari/537.36")
        self._context = self._browser.new_context(
            user_agent=user_agent,
            locale="pt-BR",
            timezone_id=s.timezone,
            viewport={"width": 1366, "height": 768},
            extra_http_headers={"accept-language": "pt-BR,pt;q=0.9,en;q=0.8"},
        )
        self._context.route(
            "**/*",
            lambda route: route.abort() if route.request.resource_type in HEAVY else route.continue_(),
        )
        self._context.set_default_navigation_timeout(s.http_timeout * 1000 * 1.5)
        self._page = self._context.new_page()
        log.info("chromium launched")
        return self._page

    def open(self, url: str):
        """Navigate and return the live Page (for page.evaluate). Raises on block/HTTP error."""
        with self._lock:
            try:
                return self._navigate(url)
            except BlockedError:
                delay = get_settings().browser_block_retry_delay
                if delay <= 0:
                    raise
                pause = random.uniform(delay * 0.75, delay * 1.25)
                log.info("block page; one retry after a pause", extra={"url": url, "sleep": round(pause)})
                time.sleep(pause)
                return self._navigate(url)

    def _navigate(self, url: str):
        page = self._ensure()
        self.limiter._wait_turn(urlparse(url).netloc)
        try:
            resp = page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            raise FetchError(f"{url}: {exc}") from exc
        status = resp.status if resp else 0
        # Give a JS challenge a moment to settle; failing to go idle is not fatal.
        with contextlib.suppress(Exception):
            page.wait_for_load_state("networkidle", timeout=8000)
        head = (page.title() or "") + " " + page.content()[:20000]
        if looks_blocked(status if status >= 400 else 200, head):
            log.warning("browser blocked", extra={"url": url, "status": status})
            raise BlockedError(f"{urlparse(url).netloc} respondeu {status} (anti-bot/captcha no navegador)")
        if status >= 400 or status == 0:
            raise FetchError(f"{url}: HTTP {status or 'sem resposta'}")
        return page

    def get_html(self, url: str) -> str:
        return self.open(url).content()

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = self._page = None
