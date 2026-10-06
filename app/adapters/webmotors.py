"""Webmotors adapter.

Search pages are Next.js and embed results in `__NEXT_DATA__` (confirmed 2026-10-06:
HTTP 200 from a residential IP, 403/PerimeterX from datacenter IPs). The exact JSON path
is located heuristically (largest array of objects that look like ads) so small frontend
changes don't break us. robots.txt disallows /api/detail/, so there is no detail fetch:
the search payload already carries description and photos.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import urlencode

from app.adapters.base import RawListing, SearchFilters, SourceAdapter, passes_filters, to_int
from app.adapters.nextdata import extract_next_data, find_listing_array, first
from app.services.normalize import norm

log = logging.getLogger(__name__)

BASE = "https://www.webmotors.com.br"
PHOTO_BASE = "https://image.webmotors.com.br/_fotos/anunciousados/gigante/"
STATE_NAMES = {"pr": "Paraná", "sc": "Santa Catarina", "sp": "São Paulo", "rs": "Rio Grande do Sul"}


def _slug(text: str | None) -> str:
    return norm(text).replace(".", "").replace(" ", "-") or "x"


def _is_ad(d: dict) -> bool:
    keys = {str(k).lower() for k in d}
    return ("uniqueid" in keys or "id" in keys) and ("specification" in keys or "prices" in keys)


class WebmotorsAdapter(SourceAdapter):
    name = "webmotors"
    label = "Webmotors"
    supports_detail = False

    def build_url(self, f: SearchFilters, page: int) -> str:
        state = f.state.lower()
        path = f"/carros/{state}/{_slug(f.brand)}/{_slug(f.model)}"
        params = {
            "tipoveiculo": "carros",
            "estadocidade": STATE_NAMES.get(state, state.upper()),
            "marca1": f.brand.upper(),
            "modelo1": f.model.upper(),
            "o": "1",  # menor preço
        }
        if f.max_price:
            params["precoate"] = f.max_price
        if f.min_year:
            params["anode"] = f.min_year
        if f.max_km:
            params["kmate"] = f.max_km
        if f.automatic_only:
            params["cambio"] = "Automática"
        if page > 1:
            params["page"] = page
        return f"{BASE}{path}?{urlencode(params)}"

    def search(self, filters: SearchFilters) -> list[RawListing]:
        seen: dict[str, RawListing] = {}
        for page in range(1, filters.max_pages + 1):
            html = self.client.get_text(self.build_url(filters, page))
            items = self.parse_search(html)
            new = [i for i in items if i.external_id not in seen]
            for i in new:
                seen[i.external_id] = i
            if not new:
                break
        return [i for i in seen.values() if passes_filters(i, filters)]

    # -- parsing ---------------------------------------------------------------
    def parse_search(self, html: str) -> list[RawListing]:
        data = extract_next_data(html)
        if data is None:
            log.warning("webmotors: __NEXT_DATA__ not found")
            return []
        return [r for ad in find_listing_array(data, _is_ad) if (r := self.parse_ad(ad))]

    def parse_ad(self, ad: dict) -> RawListing | None:
        ext_id = first(ad, "UniqueId", "Id", "uniqueId")
        if ext_id is None:
            return None
        spec = first(ad, "Specification", default={}) or {}
        seller = first(ad, "Seller", default={}) or {}
        brand = first(spec, "Make.Value", "Make", "Brand")
        model = first(spec, "Model.Value", "Model")
        version = first(spec, "Version.Value", "Version")
        year_fab = to_int(first(spec, "YearFabrication", "YearFab"))
        year_model = to_int(first(spec, "YearModel"))
        doors = first(spec, "NumberPorts", "Doors")

        state_raw = first(seller, "State", default="") or ""
        m = re.search(r"\(([A-Z]{2})\)", state_raw)
        state = (m.group(1) if m else state_raw[:2]).lower() or None

        photos = []
        for p in first(ad, "Media.Photos", "Photos", default=[]) or []:
            path = first(p, "PhotoPath", "Url", "Path") if isinstance(p, dict) else p
            if path:
                photos.append(path if str(path).startswith("http") else PHOTO_BASE + str(path))

        url = first(ad, "Url", "Link")
        if not url:
            yrs = f"{year_fab or ''}-{year_model or ''}".strip("-")
            url = (
                f"{BASE}/comprar/{_slug(brand)}/{_slug(model)}/{_slug(version)}/"
                f"{doors or 4}-portas/{yrs}/{ext_id}"
            )
        elif url.startswith("/"):
            url = BASE + url

        published = first(ad, "PublishDate", "CreatedDate", "Created")
        published_at = None
        if isinstance(published, str):
            try:
                published_at = datetime.fromisoformat(published.replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
            except ValueError:
                published_at = None

        seller_type_raw = first(seller, "SellerType", "Type")
        seller_type = {"PJ": "loja", "PF": "particular"}.get(str(seller_type_raw).upper())

        return RawListing(
            source=self.name,
            external_id=str(ext_id),
            url=url,
            title=first(spec, "Title", default="") or " ".join(filter(None, [brand, model, version])),
            brand=brand,
            model=model,
            version=version,
            year_fab=year_fab,
            year_model=year_model,
            km=to_int(first(spec, "Odometer", "Km")),
            transmission=first(spec, "Transmission", "Gearbox"),
            fuel=first(spec, "Fuel", "FuelType"),
            color=first(spec, "Color.Primary", "Color"),
            price=to_int(first(ad, "Prices.Price", "Prices.SearchPrice", "Price")),
            city=first(seller, "City"),
            state=state,
            seller_type=seller_type,
            seller_name=first(seller, "FantasyName", "Name"),
            photos=photos,
            description=first(ad, "LongComment", "Comment", "Description", default="") or "",
            published_at=published_at,
            has_detail=True,
        )
