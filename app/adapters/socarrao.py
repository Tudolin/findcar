"""SóCarrão adapter (verified live 2026-10-07).

* Plain HTTP works (no bot wall). Site is Nuxt 3: every page embeds
  `<script id="__NUXT_DATA__">` (devalue format, see nuxt.py) with the full, structured
  search results — brand, model, FIPE-style version, transmission, fuel, color, km, years,
  price, seller type, city and photo URLs.
* robots.txt DISALLOWS filter query strings (price, year, km, order…), so only the path
  form is used: `/{uf}/{cidade}/{marca}/{modelo}` (100 km around the city) and `?pagina=N`,
  which is allowed. Price/year/km/city are filtered locally.
* Ad page `/{uf}/{cidade}/{modelo}/{cor}/{id}` carries `vehicle.description` in the payload.
"""

from __future__ import annotations

import logging
import re

from selectolax.lexbor import LexborHTMLParser

from app.adapters.base import RawListing, SearchFilters, SourceAdapter, paginate, passes_filters, to_int
from app.adapters.nuxt import extract_nuxt_data
from app.services.normalize import norm

log = logging.getLogger(__name__)

BASE = "https://www.socarrao.com.br"


def _slug(text: str | None) -> str:
    return norm(text).replace(".", "").replace(" ", "-") or "x"


def _name(obj, key: str = "name"):
    return obj.get(key) if isinstance(obj, dict) else None


def _find_results(data) -> list[dict]:
    """The search store is keyed by a random hash; find it by shape."""
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            res = cur.get("results")
            if isinstance(res, list) and res and isinstance(res[0], dict) and "priceInfo" in res[0]:
                return res
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return []


def _find_vehicle(data) -> dict | None:
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            v = cur.get("vehicle")
            if isinstance(v, dict) and (v.get("vehicleId") or v.get("id")):
                return v
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


class SoCarraoAdapter(SourceAdapter):
    name = "socarrao"
    label = "SóCarrão"

    def build_url(self, f: SearchFilters, page: int) -> str:
        url = f"{BASE}/{f.state.lower()}/{_slug(f.city)}/{_slug(f.brand)}/{_slug(f.model)}"
        return f"{url}?pagina={page}" if page > 1 else url

    def fetch_search_html(self, f: SearchFilters) -> str:
        return self.client.get_text(self.build_url(f, 1))

    def search(self, filters: SearchFilters) -> list[RawListing]:
        def page(n: int) -> list[RawListing]:
            html = self.client.get_text(self.build_url(filters, n))
            return [i for i in self.parse_search(html) if passes_filters(i, filters)]

        return paginate(page, filters.max_pages)

    def fetch_detail(self, url: str) -> RawListing | None:
        return self.parse_detail(self.client.get_text(url), url)

    # -- parsing -------------------------------------------------------------------
    def parse_search(self, html: str) -> list[RawListing]:
        data = extract_nuxt_data(html)
        if data is None:
            log.warning("socarrao: __NUXT_DATA__ not found")
            return []
        # Ad URLs are only in the markup / JSON-LD: map id → href.
        urls: dict[str, str] = {}
        for a in LexborHTMLParser(html).css("a[href]"):
            href = a.attributes.get("href") or ""
            m = re.search(r"/(\d{5,})/?$", href)
            if m and m.group(1) not in urls:
                urls[m.group(1)] = href if href.startswith("http") else BASE + href
        out = []
        for item in _find_results(data):
            r = self.parse_item(item, urls)
            if r:
                out.append(r)
        return out

    def parse_item(self, it: dict, urls: dict[str, str] | None = None, description: str = "") -> RawListing | None:
        ext_id = str(it.get("id") or "")
        price = to_int((it.get("priceInfo") or {}).get("price"))
        if not ext_id or not price:
            return None
        loc = it.get("location") or {}
        city = _name(loc.get("city"))
        uf = (_name(loc.get("state"), "uf") or "").lower() or None
        brand, model = _name(it.get("brand")), _name(it.get("model"))
        version = _name(it.get("version"))
        color = _name(it.get("color"))
        url = (urls or {}).get(ext_id) or (
            f"{BASE}/{uf or 'pr'}/{_slug(city)}/{_slug(model)}/{_slug(color)}/{ext_id}")
        user = it.get("user") or {}
        seller_type = {"PROFISSIONAL": "loja", "PARTICULAR": "particular"}.get(str(user.get("type")).upper())
        return RawListing(
            source=self.name, external_id=ext_id, url=url,
            title=" ".join(filter(None, [brand, model, version])),
            brand=brand, model=model, version=version,
            year_fab=to_int(it.get("manufactureYear")), year_model=to_int(it.get("modelYear")),
            km=to_int(it.get("km")),
            transmission=_name(it.get("transmission")), fuel=_name(it.get("fuel")), color=color,
            price=price, city=city, state=uf,
            seller_type=seller_type, seller_name=user.get("name"),
            seller_phone=re.sub(r"\D", "", str(user.get("whatsapp") or user.get("phone") or "")) or None,
            photos=[p for p in it.get("vehicleImages") or [] if isinstance(p, str)],
            description=description, has_detail=bool(description),
        )

    def parse_detail(self, html: str, url: str) -> RawListing | None:
        """The ad page uses its own schema (vehicleId, brandName, gear, mileage, isReseller…)."""
        data = extract_nuxt_data(html)
        v = _find_vehicle(data) if data is not None else None
        if not v:
            return None
        price = v.get("price")
        price = to_int(price.get("amount") if isinstance(price, dict) else price)
        reseller = v.get("isReseller", v.get("isResale"))
        return RawListing(
            source=self.name, external_id=str(v.get("vehicleId") or v.get("id")), url=url,
            title=" ".join(filter(None, [v.get("brandName"), v.get("modelName"), v.get("versionName")])),
            brand=v.get("brandName"), model=v.get("modelName"), version=v.get("versionName"),
            year_fab=to_int(v.get("manufacturerYear")), year_model=to_int(v.get("modelYear")),
            km=to_int(v.get("mileage")), transmission=v.get("gear"), fuel=v.get("fuel"),
            color=v.get("color") if isinstance(v.get("color"), str) else _name(v.get("color")),
            price=price, city=v.get("cityName"), state=(v.get("uf") or "").lower() or None,
            seller_type=None if reseller is None else ("loja" if reseller else "particular"),
            photos=[p for p in v.get("photos") or [] if isinstance(p, str)],
            description=(v.get("description") or "").replace("\r\n", "\n").strip(),
            has_detail=True,
        )
