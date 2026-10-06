"""OLX adapter.

STATUS 2026-10-06: OLX answers 403 (Cloudflare "Attention Required") even from a
residential IP, including robots.txt. The source is therefore seeded DISABLED and the
parser below targets the documented Next.js `__NEXT_DATA__` shape (props.pageProps.ads)
but has NOT been validated against a live response. Run `python -m app.cli probe olx`
from the homelab to check; if blocked, keep it disabled — we do not bypass anti-bot.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from urllib.parse import urlencode

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.adapters.base import RawListing, SearchFilters, SourceAdapter, passes_filters, to_int
from app.adapters.nextdata import extract_next_data, find_listing_array, first
from app.services.normalize import norm

log = logging.getLogger(__name__)

BASE = "https://www.olx.com.br"


def _slug(text: str) -> str:
    return norm(text).replace(".", "").replace(" ", "-")


def _is_ad(d: dict) -> bool:
    return ("listId" in d or "listid" in d) and ("subject" in d or "title" in d)


def _props(ad: dict) -> dict[str, str]:
    out = {}
    for p in ad.get("properties") or []:
        if isinstance(p, dict) and p.get("name"):
            out[p["name"]] = p.get("value")
    return out


class OlxAdapter(SourceAdapter):
    name = "olx"
    label = "OLX"

    def build_url(self, f: SearchFilters, page: int) -> str:
        path = (
            f"/autos-e-pecas/carros-vans-e-utilitarios/{_slug(f.brand)}/{_slug(f.model)}"
            f"/estado-{f.state.lower()}"
        )
        params: dict = {"sf": 1}  # sort: menor preço (unverified)
        if f.max_price:
            params["pe"] = f.max_price
        if page > 1:
            params["o"] = page
        return f"{BASE}{path}?{urlencode(params)}"

    def search(self, filters: SearchFilters) -> list[RawListing]:
        seen: dict[str, RawListing] = {}
        for page in range(1, filters.max_pages + 1):
            items = self.parse_search(self.client.get_text(self.build_url(filters, page)))
            new = [i for i in items if i.external_id not in seen]
            for i in new:
                seen[i.external_id] = i
            if not new:
                break
        return [i for i in seen.values() if passes_filters(i, filters)]

    def parse_search(self, html: str) -> list[RawListing]:
        data = extract_next_data(html)
        if data is None:
            log.warning("olx: __NEXT_DATA__ not found")
            return []
        ads = first(data, "props.pageProps.ads") or find_listing_array(data, _is_ad)
        return [r for ad in ads if isinstance(ad, dict) and (r := self.parse_ad(ad))]

    def parse_ad(self, ad: dict) -> RawListing | None:
        if not _is_ad(ad) or not ad.get("url"):
            return None
        props = _props(ad)
        loc = ad.get("locationDetails") or {}
        city, neighborhood = loc.get("municipality"), loc.get("neighbourhood")
        if not city and ad.get("location"):
            parts = [p.strip() for p in str(ad["location"]).split(",")]
            city = parts[0]
            neighborhood = parts[1] if len(parts) > 1 else None
        published = None
        if isinstance(ad.get("date"), int | float):
            published = datetime.fromtimestamp(ad["date"], UTC).replace(tzinfo=None)
        photos = []
        for img in ad.get("images") or []:
            if isinstance(img, dict):
                src = img.get("original") or img.get("originalWebp") or img.get("thumbnail")
                if src:
                    photos.append(src)
        year = to_int(props.get("regdate"))
        return RawListing(
            source=self.name,
            external_id=str(ad.get("listId") or ad.get("listid")),
            url=ad["url"],
            title=ad.get("subject") or ad.get("title") or "",
            brand=props.get("vehicle_brand"),
            model=props.get("vehicle_model"),
            version=props.get("vehicle_version"),
            year_fab=year,
            year_model=year,
            km=to_int(props.get("mileage")),
            transmission=props.get("gearbox"),
            fuel=props.get("fuel"),
            color=props.get("carcolor"),
            price=to_int(ad.get("price")),
            city=city,
            neighborhood=neighborhood,
            state=(loc.get("uf") or "").lower() or None,
            seller_type="loja" if ad.get("professionalAd") else "particular",
            photos=photos,
            published_at=published,
        )

    def fetch_detail(self, url: str) -> RawListing | None:
        html = self.client.get_text(url)
        description = ""
        data = extract_next_data(html)
        if data:
            description = first(data, "props.pageProps.ad.description", default="") or ""
        if not description:
            node = HTMLParser(html).css_first("#initial-data")
            if node and node.attributes.get("data-json"):
                try:
                    blob = json.loads(node.attributes["data-json"])
                    description = first(blob, "ad.description", default="") or ""
                except json.JSONDecodeError:
                    pass
        if not description:
            m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
            if m:
                description = json.loads(f'"{m.group(1)}"')
        return RawListing(source=self.name, external_id="", url=url, description=description,
                          has_detail=True)
