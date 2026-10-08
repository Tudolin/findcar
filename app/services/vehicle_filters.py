"""Vehicle list filters: tolerant parsing of query params, SQL, and removable "chips".

Every field arrives as a string because the HTMX form submits all of its inputs, empty
ones included (`max_price=`). Nothing here may raise on bad input — a filter that can't
be parsed is simply ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import timedelta
from urllib.parse import urlencode

from sqlmodel import col, exists, or_, select

from app.core.timeutil import utcnow
from app.models import STAGE_LABELS, Listing, Stage, Vehicle

PAGE_SIZE = 60

SORTS = {
    "score": ("Melhor score", lambda: (col(Vehicle.score).desc().nulls_last(),)),
    "price": ("Menor preço", lambda: (col(Vehicle.price).asc().nulls_last(),)),
    "fipe": ("Mais abaixo da FIPE", lambda: (col(Vehicle.fipe_diff_pct).asc().nulls_last(),)),
    "km": ("Menor km", lambda: (col(Vehicle.km).asc().nulls_last(),)),
    "year": ("Mais novo", lambda: (col(Vehicle.year_model).desc().nulls_last(),)),
    "drop": ("Maior queda de preço", lambda: (col(Vehicle.price_drop).desc().nulls_last(),)),
    "recent": ("Mais recentes", lambda: (col(Vehicle.created_at).desc(),)),
    "oldest": ("Mais tempo anunciado", lambda: (col(Vehicle.listed_since).asc().nulls_last(),)),
}
STATUSES = {"active": "Ativos", "favorites": "Favoritos", "inactive": "Inativos", "all": "Todos"}
NEW_WINDOWS = {"1": "Novos em 24h", "7": "Novos em 7 dias"}


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else None


def _bool(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes", "sim"}


@dataclass
class VehicleFilter:
    q: str = ""
    model: str = ""
    min_price: int | None = None
    max_price: int | None = None
    min_year: int | None = None
    max_year: int | None = None
    max_km: int | None = None
    transmission: str = ""  # automatico | manual
    seller: str = ""  # loja | particular
    source: str = ""
    city: str = ""
    stage: str = ""
    min_score: int | None = None
    below_fipe: bool = False
    no_flags: bool = False
    dropped: bool = False
    new: str = ""  # "1" | "7" days
    status: str = "active"
    sort: str = "score"
    offset: int = 0

    @classmethod
    def from_params(cls, params) -> VehicleFilter:
        f = cls()
        for name in ("q", "model", "transmission", "seller", "source", "city", "stage", "new"):
            setattr(f, name, (params.get(name) or "").strip())
        for name in ("min_price", "max_price", "min_year", "max_year", "max_km", "min_score"):
            setattr(f, name, _int(params.get(name)))
        for name in ("below_fipe", "no_flags", "dropped"):
            setattr(f, name, _bool(params.get(name)))
        f.status = params.get("status") if params.get("status") in STATUSES else "active"
        f.sort = params.get("sort") if params.get("sort") in SORTS else "score"
        f.offset = _int(params.get("offset")) or 0
        if f.transmission not in ("", "automatico", "manual"):
            f.transmission = ""
        if f.seller not in ("", "loja", "particular"):
            f.seller = ""
        if f.stage and f.stage not in {s.value for s in Stage}:
            f.stage = ""
        if f.new not in NEW_WINDOWS:
            f.new = ""
        return f

    # -- SQL -----------------------------------------------------------------------
    def statement(self):
        V = Vehicle
        stmt = select(V)
        if self.status == "active":
            stmt = stmt.where(V.active)
        elif self.status == "inactive":
            stmt = stmt.where(V.active == False)  # noqa: E712
        elif self.status == "favorites":
            stmt = stmt.where(V.favorite)
        if self.model:
            stmt = stmt.where(V.model == self.model)
        if self.min_price:
            stmt = stmt.where(V.price >= self.min_price)
        if self.max_price:
            stmt = stmt.where(V.price <= self.max_price)
        if self.min_year:
            stmt = stmt.where(V.year_model >= self.min_year)
        if self.max_year:
            stmt = stmt.where(V.year_model <= self.max_year)
        if self.max_km:
            stmt = stmt.where(V.km <= self.max_km)
        if self.transmission:
            stmt = stmt.where(V.transmission == self.transmission)
        if self.seller:
            stmt = stmt.where(V.seller_type == self.seller)
        if self.city:
            stmt = stmt.where(col(V.city).ilike(self.city))
        if self.min_score:
            stmt = stmt.where(V.score >= self.min_score)
        if self.below_fipe:
            stmt = stmt.where(V.fipe_diff_pct < 0)
        if self.no_flags:
            stmt = stmt.where(or_(V.red_flags == [], col(V.red_flags).is_(None)))
        if self.dropped:
            stmt = stmt.where(V.price_drop > 0)
        if self.new:
            stmt = stmt.where(V.created_at >= utcnow() - timedelta(days=int(self.new)))
        if self.stage:
            stmt = stmt.where(V.stage == self.stage)
        elif self.status != "favorites":
            stmt = stmt.where(V.stage != Stage.DESCARTADO)
        if self.source:
            stmt = stmt.where(exists().where(Listing.vehicle_id == V.id, Listing.source == self.source))
        if self.q:
            like = f"%{self.q}%"
            stmt = stmt.where(or_(col(V.version).ilike(like), col(V.model).ilike(like),
                                  col(V.brand).ilike(like), col(V.notes).ilike(like),
                                  col(V.city).ilike(like), col(V.color).ilike(like)))
        return stmt

    def ordered(self, stmt):
        return stmt.order_by(*SORTS[self.sort][1](), col(Vehicle.id).desc())

    # -- URL / chips -----------------------------------------------------------------
    def params(self, **override) -> dict:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data.update(override)
        out = {}
        for k, v in data.items():
            if k == "offset" or v in (None, "", False, 0):
                continue
            if k == "status" and v == "active":
                continue
            if k == "sort" and v == "score":
                continue
            out[k] = "1" if v is True else v
        return out

    def url(self, **override) -> str:
        p = self.params(**override)
        return "/vehicles" + ("?" + urlencode(p) if p else "")

    def more_url(self, offset: int) -> str:
        p = self.params()
        p["offset"] = offset
        return "/vehicles?" + urlencode(p)

    def chips(self) -> list[tuple[str, str]]:
        """(label, url-without-this-filter) for every active filter."""
        def money(v):
            return f"R$ {v:,.0f}".replace(",", ".")

        labels = {
            "q": lambda v: f"“{v}”", "model": lambda v: v,
            "min_price": lambda v: f"≥ {money(v)}", "max_price": lambda v: f"≤ {money(v)}",
            "min_year": lambda v: f"ano ≥ {v}", "max_year": lambda v: f"ano ≤ {v}",
            "max_km": lambda v: f"≤ {v:,} km".replace(",", "."),
            "transmission": lambda v: {"automatico": "Automático", "manual": "Manual"}[v],
            "seller": lambda v: {"loja": "Loja", "particular": "Particular"}[v],
            "source": lambda v: {"olx": "OLX", "webmotors": "Webmotors", "socarrao": "SóCarrão"}.get(v, v),
            "city": lambda v: v, "stage": lambda v: STAGE_LABELS.get(Stage(v), v),
            "min_score": lambda v: f"score ≥ {v}", "below_fipe": lambda v: "Abaixo da FIPE",
            "no_flags": lambda v: "Sem red flags", "dropped": lambda v: "Baixou de preço",
            "new": lambda v: NEW_WINDOWS[v], "status": lambda v: STATUSES[v],
        }
        out = []
        for key in self.params():
            if key in labels:
                reset = "active" if key == "status" else None
                out.append((labels[key](getattr(self, key)), self.url(**{key: reset})))
        return out

    @property
    def advanced_count(self) -> int:
        keys = ("min_price", "min_year", "max_year", "transmission", "seller", "city", "min_score",
                "below_fipe", "no_flags", "dropped", "new", "stage")
        return sum(1 for k in keys if getattr(self, k) not in (None, "", False))
