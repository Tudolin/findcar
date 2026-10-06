"""FIPE prices via the free parallelum API (https://fipe.parallelum.com.br/api/v2).

Every response is cached in `fipe_cache`; the API has a daily quota without token.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import Session

from app.adapters.base import to_int
from app.core.config import get_settings
from app.core.http import PoliteClient
from app.core.timeutil import utcnow
from app.models import FipeCache, Vehicle
from app.services.normalize import norm

log = logging.getLogger(__name__)

LIST_TTL = timedelta(days=30)


@dataclass
class FipeMatch:
    code: str
    model_name: str
    price: int
    reference: str


class FipeService:
    def __init__(self, session: Session, client: PoliteClient | None = None,
                 price_ttl_days: int = 15) -> None:
        s = get_settings()
        self.session = session
        self.base = s.fipe_base_url.rstrip("/")
        headers = {"X-Subscription-Token": s.fipe_token} if s.fipe_token else {}
        self.client = client or PoliteClient(
            min_delay=0.4, max_delay=0.8, cache_ttl=0, headers=headers
        )
        self.price_ttl = timedelta(days=price_ttl_days)

    def _get(self, path: str, ttl: timedelta):
        row = self.session.get(FipeCache, path)
        if row and utcnow() - row.fetched_at < ttl:
            return row.payload
        payload = self.client.get_json(f"{self.base}{path}", use_cache=False)
        if row is None:
            row = FipeCache(key=path, payload=payload)
        else:
            row.payload, row.fetched_at = payload, utcnow()
        self.session.add(row)
        self.session.flush()
        return payload

    def brand_code(self, brand: str) -> str | None:
        target = norm(brand)
        brands = self._get("/cars/brands", LIST_TTL)
        for b in brands:
            if norm(b["name"]) == target:
                return b["code"]
        for b in brands:  # "GM - Chevrolet", "VW - VolksWagen"
            if target in norm(b["name"]).split():
                return b["code"]
        return None

    @staticmethod
    def rank_models(models: list[dict], model: str, version: str | None,
                    transmission: str | None) -> list[dict]:
        prefix = norm(model)
        vtokens = set(norm(version).split())
        ranked = []
        for m in models:
            name = norm(m["name"])
            if not (name == prefix or name.startswith(prefix + " ")):
                continue
            tokens = set(re.split(r"[ /]+", name))
            score = 0.0
            for t in vtokens & tokens:
                score += 3 if re.fullmatch(r"\d\.\d", t) else 1
            is_aut = bool(re.search(r"\baut\b", name))
            if transmission == "automatico":
                score += 2 if is_aut else -3
            elif transmission == "manual":
                score += 2 if not is_aut else -3
            ranked.append((score, m))
        ranked.sort(key=lambda x: -x[0])
        return [m for _, m in ranked]

    def match(self, brand: str, model: str, version: str | None, transmission: str | None,
              year_model: int) -> FipeMatch | None:
        bcode = self.brand_code(brand)
        if not bcode:
            return None
        models = self._get(f"/cars/brands/{bcode}/models", LIST_TTL)
        for cand in self.rank_models(models, model, version, transmission)[:10]:
            years = self._get(f"/cars/brands/{bcode}/models/{cand['code']}/years", LIST_TTL)
            year_codes = [y["code"] for y in years if str(y["code"]).startswith(f"{year_model}-")]
            if not year_codes:
                continue
            ycode = sorted(year_codes, key=lambda c: c != f"{year_model}-1")[0]
            data = self._get(
                f"/cars/brands/{bcode}/models/{cand['code']}/years/{ycode}", self.price_ttl
            )
            price = to_int(data.get("price"))
            if price:
                return FipeMatch(data.get("codeFipe", ""), data.get("model", cand["name"]),
                                 price, data.get("referenceMonth", ""))
        return None

    def update_vehicle(self, v: Vehicle) -> bool:
        if not (v.brand and v.model and v.year_model):
            return False
        try:
            m = self.match(v.brand, v.model, v.version, v.transmission, v.year_model)
        except Exception as exc:  # FIPE is best-effort; never break a run
            log.warning("fipe lookup failed", extra={"vehicle": v.id, "err": str(exc)})
            return False
        if not m:
            return False
        v.fipe_code, v.fipe_model, v.fipe_price, v.fipe_ref = m.code, m.model_name, m.price, m.reference
        v.fipe_diff_pct = round((v.price - m.price) / m.price * 100, 1) if v.price else None
        return True
