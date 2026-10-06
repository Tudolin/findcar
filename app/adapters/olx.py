"""OLX adapter — same strategy as findhome's OLX parser (verified live 2026-10-06).

* OLX answers 403 (Cloudflare) to plain HTTP clients, whatever the IP: the wall checks
  the TLS/HTTP fingerprint. A real headless Chromium gets the normal page, so OLX is
  always read through `self.browser`. A challenge/captcha page still raises BlockedError.
* `__NEXT_DATA__` no longer exists on search pages. Listings are server-rendered as
  `section.olx-adcard`; specs come from `aria-label`s ("235000 quilômetros rodados",
  "Cor Cinza"), which exist for screen readers and are stabler than CSS classes. The ad id
  is the trailing number of the ad URL.
* The ad page carries `<script id="initial-data" data-json=…>` with the full ad: description,
  color, gearbox, fuel, professionalAd, every photo and even `has_auction`.
"""

from __future__ import annotations

import contextlib
import html as htmllib
import json
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser

from app.adapters.base import (
    RawListing,
    SearchFilters,
    SourceAdapter,
    paginate,
    passes_filters,
    to_int,
)
from app.core.config import get_settings
from app.services.normalize import norm

log = logging.getLogger(__name__)

BASE = "https://www.olx.com.br"
CATEGORY = "/autos-e-pecas/carros-vans-e-utilitarios"
# OLX groups cities into regions; searching the region keeps results local.
REGIONS = {("pr", "curitiba"): "regiao-de-curitiba-e-paranagua"}
MONTHS = {m: i for i, m in enumerate(
    ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"], 1)}


def _slug(text: str) -> str:
    return norm(text).replace(".", "").replace(" ", "-")


def _id_from_href(href: str) -> str:
    m = re.search(r"(\d{6,})(?:[/?#]|$)", href)
    return m.group(1) if m else ""


def _upgrade_image(url: str) -> str:
    return re.sub(r"/thumbs\d+x\d+/", "/images/", url)


def _year_from_title(title: str) -> int | None:
    """OLX titles end with the year ("… Aut. 2010"); some sellers append text after it."""
    years = re.findall(r"(?<![\d.])((?:19|20)\d{2})(?![\d.])", title)
    return int(years[-1]) if years else None


def _transmission_from_title(title: str) -> str | None:
    """Titles use FIPE naming, where only automatic versions say "Aut." (or CVT)."""
    if re.search(r"\b(aut|automatic[oa]|autom[aá]tico|cvt|mec|manual)\b", title, re.I):
        return title
    if re.search(r"\d\.\d", title) and re.search(r"\b\d{1,2}v\b", title, re.I):
        return "Manual (inferido do título no padrão FIPE)"
    return None


def _split_location(value: str) -> tuple[str | None, str | None, str | None]:
    """'Curitiba, Xaxim' | 'Curitiba - PR' | 'Curitiba - PR, Centro' → city, bairro, uf."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if not parts:
        return None, None, None
    city, uf = parts[0], None
    m = re.match(r"^(.*?)\s*-\s*([A-Za-z]{2})$", city)
    if m:
        city, uf = m.group(1).strip(), m.group(2).lower()
    return city, (", ".join(parts[1:]) or None), uf


def parse_card_date(text: str, now: datetime | None = None) -> datetime | None:
    """'Hoje, 16:17' / 'Ontem, 10:00' / '12 de out, 10:00' (local time) → naive UTC."""
    tz = ZoneInfo(get_settings().timezone)
    now = now or datetime.now(tz)
    t = norm(text)
    hm = re.search(r"(\d{1,2}):(\d{2})", text)
    hh, mm = (int(hm.group(1)), int(hm.group(2))) if hm else (12, 0)
    if t.startswith("hoje"):
        day = now.date()
    elif t.startswith("ontem"):
        day = (now - timedelta(days=1)).date()
    else:
        m = re.search(r"(\d{1,2}) de ([a-z]{3})", t)
        if not m or m.group(2) not in MONTHS:
            return None
        year = now.year
        month = MONTHS[m.group(2)]
        if month > now.month:  # "28 de dez" seen in January
            year -= 1
        try:
            day = datetime(year, month, int(m.group(1))).date()
        except ValueError:
            return None
    local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


class OlxAdapter(SourceAdapter):
    name = "olx"
    label = "OLX"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._url_index = 0  # remembered across pages: no point re-testing dead URL shapes

    def urls(self, f: SearchFilters, page: int) -> list[str]:
        params: dict = {"o": page}
        if f.max_price:
            params["pe"] = f.max_price
        uf = f.state.lower()
        model_path = f"{CATEGORY}/{_slug(f.brand)}/{_slug(f.model)}/estado-{uf}"
        out = []
        region = REGIONS.get((uf, norm(f.city)))
        if region:
            out.append(f"{BASE}{model_path}/{region}?{urlencode(params)}")
        out.append(f"{BASE}{model_path}?{urlencode(params)}")
        # Model slug unknown to OLX → free-text search in the state.
        out.append(f"{BASE}{CATEGORY}/estado-{uf}?{urlencode({**params, 'q': f'{f.brand} {f.model}'})}")
        return out

    def build_url(self, f: SearchFilters, page: int) -> str:  # used by the CLI
        return self.urls(f, page)[0]

    def fetch_search_html(self, f: SearchFilters) -> str:
        return self.browser.get_html(self.build_url(f, 1))

    def _search_page(self, f: SearchFilters, page: int) -> list[RawListing]:
        candidates = self.urls(f, page)
        for attempt in range(len(candidates)):
            idx = (self._url_index + attempt) % len(candidates)
            html = self.browser.get_html(candidates[idx])
            items = [i for i in self.parse_search(html, brand=f.brand) if passes_filters(i, f)]
            if items or self.count_cards(html):
                if idx != self._url_index:
                    log.info("olx url scheme", extra={"url": candidates[idx]})
                    self._url_index = idx
                return items
        return []

    def search(self, filters: SearchFilters) -> list[RawListing]:
        return paginate(lambda p: self._search_page(filters, p), filters.max_pages)

    # -- parsing -----------------------------------------------------------------
    @staticmethod
    def count_cards(html: str) -> int:
        return len(LexborHTMLParser(html).css("section[class*='adcard']"))

    def parse_search(self, html: str, brand: str | None = None) -> list[RawListing]:
        tree = LexborHTMLParser(html)
        out: dict[str, RawListing] = {}
        for card in tree.css("section[class*='adcard']"):  # also matches section.olx-adcard
            item = self.parse_card(card, brand)
            if item and item.external_id not in out:
                out[item.external_id] = item
        return list(out.values())

    def parse_card(self, card, brand: str | None) -> RawListing | None:
        link = card.css_first("a[data-testid='adcard-link']") or card.css_first("a[href]")
        href = (link.attributes.get("href") if link else "") or ""
        ext_id = _id_from_href(href)
        if not ext_id:
            return None
        title_node = card.css_first(".olx-adcard__title")
        title = (title_node.text(strip=True) if title_node else "") or (link.attributes.get("title") or "")
        title = re.sub(r"\s+", " ", title).strip()
        price_node = card.css_first(".olx-adcard__price")
        price = to_int(price_node.text(strip=True)) if price_node else None
        if not price:
            return None
        loc = card.css_first(".olx-adcard__location")
        city, neighborhood, uf = _split_location(loc.text(strip=True) if loc else "")
        km = color = None
        for d in card.css(".olx-adcard__detail"):
            label = d.attributes.get("aria-label") or d.text(strip=True)
            low = label.lower()
            if "quilômetro" in low or re.search(r"\bkm\b", low):
                km = to_int(label)
            elif low.startswith("cor "):
                color = label[4:].strip()
        photos: list[str] = []
        for img in card.css("img"):
            for attr in ("src", "data-src", "data-lazy-src"):
                v = img.attributes.get(attr)
                if v and re.search(r"img\.olx\.com\.br", v) and not re.search(r"logo|sprite|icon", v):
                    photos.append(_upgrade_image(v))
        date = card.css_first(".olx-adcard__date")
        year = _year_from_title(title)
        return RawListing(
            source=self.name,
            external_id=ext_id,
            url=href if href.startswith("http") else BASE + href,
            title=title,
            brand=brand,
            model=None,  # resolved from the title via aliases ("Honda Fit LX 1.4/ … Mec. 2004")
            version=re.sub(r"\s*(?<![\d.])(?:19|20)\d{2}(?![\d.]).*$", "", title),
            year_fab=year,
            year_model=year,
            km=km,
            transmission=_transmission_from_title(title),  # the ad page confirms it later
            color=color,
            price=price,
            city=city,
            neighborhood=neighborhood,
            state=uf,
            photos=list(dict.fromkeys(photos)),
            published_at=parse_card_date(date.text(strip=True)) if date else None,
        )

    def fetch_detail(self, url: str) -> RawListing | None:
        return self.parse_detail(self.browser.get_html(url), url)

    def parse_detail(self, html: str, url: str) -> RawListing | None:
        node = LexborHTMLParser(html).css_first("#initial-data")
        if not node or not node.attributes.get("data-json"):
            return None
        try:
            ad = json.loads(node.attributes["data-json"]).get("ad") or {}
        except json.JSONDecodeError:
            return None
        props = {p.get("name"): p.get("value") for p in ad.get("properties") or [] if isinstance(p, dict)}
        desc = ad.get("description") or ad.get("body") or ""
        desc = re.sub(r"<br\s*/?>", "\n", desc)
        desc = htmllib.unescape(re.sub(r"<[^>]+>", "", desc)).strip()
        if str(props.get("has_auction", "")).lower() == "sim":
            desc = "Veículo de leilão (informado no anúncio OLX).\n" + desc
        loc = ad.get("location") or {}
        photos = [i.get("original") for i in ad.get("images") or [] if isinstance(i, dict) and i.get("original")]
        published = None
        if ad.get("listTime"):
            with contextlib.suppress(ValueError):
                published = datetime.fromisoformat(ad["listTime"].replace("Z", "+00:00")).replace(tzinfo=None)
        year = to_int(props.get("regdate"))
        return RawListing(
            source=self.name,
            external_id=str(ad.get("listId") or _id_from_href(url)),
            url=url,
            title=ad.get("subject") or "",
            brand=props.get("vehicle_brand"),
            model=props.get("vehicle_model"),
            year_fab=year,
            year_model=year,
            km=to_int(props.get("mileage")),
            transmission=props.get("gearbox"),
            fuel=props.get("fuel"),
            color=props.get("carcolor"),
            price=to_int(ad.get("priceValue") or ad.get("price")),
            city=loc.get("municipality"),
            neighborhood=loc.get("neighbourhood"),
            state=(loc.get("uf") or "").lower() or None,
            seller_type="loja" if ad.get("professionalAd") else "particular",
            seller_name=(ad.get("user") or {}).get("name"),
            photos=photos,
            description=desc,
            published_at=published,
            has_detail=True,
        )
