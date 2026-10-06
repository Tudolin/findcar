"""Webmotors adapter (verified live 2026-10-06).

* Search page `/carros/{uf}/{marca}/{modelo}?…` — filters `precoate`, `anode`, pagination `page`.
* Listings are rendered as cards (CSS-module classes like `vehicle-card-desktop_Container__x`,
  whose hash suffix changes on every deploy, so cards are found from their `/comprar/…/{id}`
  link and read by tag: h2 = marca+modelo, h3 = versão, p = ano / km / cidade (UF) / preço).
  `__NEXT_DATA__.props.pageProps.catalogProps.items` (schema.org: name, price, url, image) is
  the fallback when the markup changes.
* Ad page `/comprar/…` has JSON-LD `Car` (cor, câmbio, combustível, km, anos) and `Product`
  (offers.seller @type AutoDealer = loja). robots.txt only disallows /api/detail/ for `*`.
* Plain HTTP works from residential IPs; PerimeterX blocks datacenter IPs and some clients.
  When HTTP is turned away (or returns a page without listings) we switch to headless
  Chromium for the rest of the run. A captcha in the browser still raises BlockedError.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlencode

from selectolax.lexbor import LexborHTMLParser

from app.adapters.base import RawListing, SearchFilters, SourceAdapter, paginate, passes_filters, to_int
from app.adapters.nextdata import extract_next_data, first
from app.core.config import get_settings
from app.core.http import BlockedError, FetchError
from app.services.normalize import norm

log = logging.getLogger(__name__)

BASE = "https://www.webmotors.com.br"
STATE_NAMES = {"pr": "Paraná", "sc": "Santa Catarina", "sp": "São Paulo", "rs": "Rio Grande do Sul"}
NO_PHOTO = "vehicle_no_photo"


def _slug(text: str | None) -> str:
    return norm(text).replace(".", "").replace(" ", "-") or "x"


def _ad_id(href: str) -> str:
    m = re.search(r"/comprar/.+/(\d{5,})(?:[/?#]|$)", href)
    return m.group(1) if m else ""


def _clean_photo(src: str) -> str:
    return src.split("?")[0]


def _years_from_url(url: str) -> tuple[int | None, int | None]:
    m = re.search(r"/((?:19|20)\d{2})(?:-((?:19|20)\d{2}))?/\d+$", url)
    if not m:
        return None, None
    fab = int(m.group(1))
    return fab, int(m.group(2)) if m.group(2) else fab


class WebmotorsAdapter(SourceAdapter):
    name = "webmotors"
    label = "Webmotors"
    supports_detail = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._use_browser = False

    def build_url(self, f: SearchFilters, page: int) -> str:
        state = f.state.lower()
        params = {
            "tipoveiculo": "carros",
            "estadocidade": STATE_NAMES.get(state, state.upper()),
            "marca1": f.brand.upper(),
            "modelo1": f.model.upper(),
        }
        if f.max_price:
            params["precoate"] = f.max_price
        if f.min_year:
            params["anode"] = f.min_year
        if page > 1:
            params["page"] = page
        return f"{BASE}/carros/{state}/{_slug(f.brand)}/{_slug(f.model)}?{urlencode(params)}"

    # -- fetching: HTTP first, browser when turned away -----------------------------
    def _get(self, url: str, needs: str) -> str:
        if not self._use_browser:
            try:
                html = self.client.get_text(url)
                if needs in html:
                    return html
                log.info("webmotors: HTTP page without data, switching to browser", extra={"url": url})
            except (BlockedError, FetchError) as exc:
                if not get_settings().browser_enabled:
                    raise
                log.info("webmotors: HTTP turned away, switching to browser", extra={"err": str(exc)})
            self._use_browser = True
        return self.browser.get_html(url)

    def fetch_search_html(self, f: SearchFilters) -> str:
        return self._get(self.build_url(f, 1), needs="/comprar/")

    def search(self, filters: SearchFilters) -> list[RawListing]:
        def page(n: int) -> list[RawListing]:
            html = self._get(self.build_url(filters, n), needs="/comprar/")
            return [i for i in self.parse_search(html) if passes_filters(i, filters)]

        return paginate(page, filters.max_pages)

    def fetch_detail(self, url: str) -> RawListing | None:
        return self.parse_detail(self._get(url, needs="application/ld+json"), url)

    # -- parsing -------------------------------------------------------------------
    def parse_search(self, html: str) -> list[RawListing]:
        tree = LexborHTMLParser(html)
        out: dict[str, RawListing] = {}
        for a in tree.css("a[href*='/comprar/']"):
            href = a.attributes.get("href") or ""
            ext_id = _ad_id(href)
            if not ext_id or ext_id in out:
                continue
            # Climb to the largest ancestor that still belongs to this ad only.
            card = a
            while card.parent is not None:
                ids = {_ad_id(x.attributes.get("href") or "") for x in card.parent.css("a[href*='/comprar/']")}
                if ids - {ext_id, ""}:
                    break
                card = card.parent
            item = self.parse_card(card, href, ext_id)
            if item:
                out[ext_id] = item
        if out:
            return list(out.values())
        return self.parse_catalog(html)

    def parse_card(self, card, href: str, ext_id: str) -> RawListing | None:
        h2 = card.css_first("h2")
        h3 = card.css_first("h3")
        head = h2.text(strip=True) if h2 else ""
        version = h3.text(strip=True) if h3 else ""
        year_fab = year_model = km = price = None
        city = state = None
        for p in card.css("p"):
            t = p.text(strip=True).replace("\xa0", " ")
            if m := re.fullmatch(r"((?:19|20)\d{2})\s*/\s*((?:19|20)\d{2})", t):
                year_fab, year_model = int(m.group(1)), int(m.group(2))
            elif re.fullmatch(r"[\d.]+\s*km", t, re.I):
                km = to_int(t)
            elif m := re.fullmatch(r"(.+?)\s*\(([A-Z]{2})\)", t):
                city, state = m.group(1).strip(), m.group(2).lower()
            elif t.startswith("R$") and price is None:
                price = to_int(t)
        if not price:
            return None
        brand, _, model = head.partition(" ")
        photos = []
        for img in card.css("img"):
            src = img.attributes.get("src") or ""
            if "image.webmotors.com.br" in src and NO_PHOTO not in src:
                photos.append(_clean_photo(src))
        url = href if href.startswith("http") else BASE + href
        return RawListing(
            source=self.name, external_id=ext_id, url=url,
            title=f"{head} {version}".strip(),
            brand=brand or None, model=model or None, version=version or None,
            year_fab=year_fab, year_model=year_model, km=km,
            transmission=version,  # "… automático" / "… manual"
            price=price, city=city, state=state,
            photos=list(dict.fromkeys(photos)),
        )

    def parse_catalog(self, html: str) -> list[RawListing]:
        """Fallback: the schema.org catalog in __NEXT_DATA__ (no km/city, but price/year/url)."""
        data = extract_next_data(html) or {}
        items = first(data, "props.pageProps.catalogProps.items", default=[]) or []
        out = []
        for it in items:
            url = first(it, "offers.url") or ""
            ext_id = _ad_id(url)
            price = to_int(first(it, "offers.price"))
            if not ext_id or not price:
                continue
            name = it.get("name") or ""
            fab, model_year = _years_from_url(url)
            parts = name.split(" ", 2)
            images = it.get("image") or []
            out.append(RawListing(
                source=self.name, external_id=ext_id, url=url, title=name,
                brand=parts[0] if parts else None, model=parts[1] if len(parts) > 1 else None,
                version=parts[2] if len(parts) > 2 else None,
                year_fab=fab, year_model=model_year, transmission=name, price=price,
                photos=[_clean_photo(i) for i in (images if isinstance(images, list) else [images])],
            ))
        return out

    def parse_detail(self, html: str, url: str) -> RawListing | None:
        tree = LexborHTMLParser(html)
        car, product = {}, {}
        for s in tree.css('script[type="application/ld+json"]'):
            try:
                data = json.loads(s.text())
            except json.JSONDecodeError:
                continue
            for obj in data if isinstance(data, list) else [data]:
                if isinstance(obj, dict) and obj.get("@type") == "Car":
                    car = obj
                elif isinstance(obj, dict) and obj.get("@type") == "Product":
                    product = obj
        if not car and not product:
            return None
        seller = first(product, "offers.seller", default={}) or {}
        seller_type = {"AutoDealer": "loja", "Person": "particular"}.get(seller.get("@type"))
        info = tree.css_first("#VehicleBasicInformation")
        extra = []
        for n in tree.css("[id*='Comment'], [id*='Observation'], [id*='Description']"):
            t = n.text(separator=" ", strip=True)
            if t and t not in extra:
                extra.append(t)
        description = "\n".join(extra + ([info.text(separator=" | ", strip=True)] if info else []))
        loc = first(seller, "address", default={}) or {}
        return RawListing(
            source=self.name, external_id=_ad_id(url), url=url,
            title=product.get("name") or "",
            transmission=car.get("vehicleTransmission"), fuel=car.get("fuelType"), color=car.get("color"),
            km=to_int(first(car, "mileageFromOdometer.value")),
            year_fab=to_int(car.get("productionDate")), year_model=to_int(car.get("vehicleModelDate")),
            price=to_int(first(product, "offers.price")),
            city=loc.get("addressLocality"), state=(loc.get("addressRegion") or "").lower() or None,
            seller_type=seller_type, seller_name=seller.get("name"),
            description=description, has_detail=True,
        )
