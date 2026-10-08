from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from app.core.http import BlockedError, PoliteClient  # noqa: F401  (re-export)


@dataclass
class SearchFilters:
    brand: str
    model: str
    max_price: int | None = None
    min_year: int | None = None
    max_km: int | None = None
    automatic_only: bool = False
    state: str = "pr"
    city: str = "curitiba"
    cities: list[str] = field(default_factory=list)  # post-filter allow-list (normalized)
    max_pages: int = 3


@dataclass
class RawListing:
    """What an adapter returns. Raw strings; normalization happens in services."""

    source: str
    external_id: str
    url: str
    title: str = ""
    brand: str | None = None
    model: str | None = None
    version: str | None = None
    year_fab: int | None = None
    year_model: int | None = None
    km: int | None = None
    transmission: str | None = None
    fuel: str | None = None
    color: str | None = None
    price: int | None = None
    city: str | None = None
    neighborhood: str | None = None
    state: str | None = None
    seller_type: str | None = None  # loja | particular
    seller_name: str | None = None
    seller_phone: str | None = None
    photos: list[str] = field(default_factory=list)
    description: str = ""
    published_at: datetime | None = None
    has_detail: bool = False  # description etc. already complete


class SourceAdapter(ABC):
    """One per site. Must be side-effect free apart from HTTP/browser navigation."""

    name: str
    label: str
    supports_detail: bool = True

    def __init__(self, client: PoliteClient | None = None, browser=None) -> None:
        self.client = client or PoliteClient()
        self._browser = browser

    @property
    def browser(self):
        """Lazily-started headless Chromium (shares the HTTP client's rate limit)."""
        if self._browser is None:
            from app.core.browser import BrowserFetcher

            self._browser = BrowserFetcher(limiter=self.client)
        return self._browser

    @abstractmethod
    def search(self, filters: SearchFilters) -> list[RawListing]: ...

    def fetch_detail(self, url: str) -> RawListing | None:
        return None

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        self.client.close()


def to_int(value) -> int | None:
    """'R$ 45.900,00' → 45900, '85.000 km' → 85000, 2015 → 2015."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    s = str(value).strip()
    if "," in s:
        s = s.split(",")[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    return int(digits) if digits else None


def passes_filters(item: RawListing, f: SearchFilters) -> bool:
    """Client-side safety net: the site's own filters are never trusted blindly."""
    from app.services.normalize import is_automatic, norm

    hay = norm(" ".join(filter(None, [item.title, item.model, item.version])))
    if f.model and hay and not re.search(rf"(?<![a-z0-9]){re.escape(norm(f.model))}(?![a-z0-9])", hay):
        return False  # e.g. an HB20S listing returned for an HB20 search
    if f.max_price and item.price and item.price > f.max_price:
        return False
    year = item.year_model or item.year_fab
    if f.min_year and year and year < f.min_year:
        return False
    if f.max_km and item.km and item.km > f.max_km:
        return False
    if f.automatic_only and not is_automatic(item.transmission or item.version or item.title):
        return False
    return not (f.cities and item.city and norm(item.city) not in {norm(c) for c in f.cities})


def paginate(fetch_page, max_pages: int) -> list[RawListing]:
    """Walk result pages until one adds nothing new (portals repeat page 1 forever)."""
    seen: dict[str, RawListing] = {}
    for page in range(1, max_pages + 1):
        items = fetch_page(page)
        fresh = [i for i in items if i.external_id not in seen]
        for i in fresh:
            seen[i.external_id] = i
        if not fresh:
            break
    return list(seen.values())
