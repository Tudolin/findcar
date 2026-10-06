"""Side-by-side comparison (≤ 6 vehicles) with best-value highlighting and CSV export."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from sqlmodel import Session, select

from app.core.timeutil import utcnow
from app.models import Listing, ModelSpec, Vehicle

MAX_COMPARE = 6


@dataclass
class Row:
    key: str
    label: str
    best: str | None  # "min" | "max" | None
    fmt: str = "text"


ROWS = [
    Row("price", "Preço", "min", "brl"),
    Row("fipe_price", "FIPE", None, "brl"),
    Row("fipe_diff_pct", "vs FIPE", "min", "pct"),
    Row("km", "Km", "min", "km"),
    Row("km_year", "Km/ano", "min", "km"),
    Row("year_model", "Ano modelo", "max"),
    Row("transmission", "Câmbio", None),
    Row("consumption_city", "Consumo cidade (km/l, aprox.)", "max", "num"),
    Row("consumption_road", "Consumo estrada (km/l, aprox.)", "max", "num"),
    Row("color", "Cor", None),
    Row("city", "Cidade", None),
    Row("seller_type", "Vendedor", None),
    Row("score", "Score", "max"),
    Row("red_flags", "Red flags", None),
    Row("links", "Anúncios", None, "links"),
]


def load(session: Session, ids: list[int]) -> list[Vehicle]:
    ids = ids[:MAX_COMPARE]
    found = {v.id: v for v in session.exec(select(Vehicle).where(Vehicle.id.in_(ids))).all()}
    return [found[i] for i in ids if i in found]


def table(session: Session, vehicles: list[Vehicle]) -> dict:
    cols = []
    for v in vehicles:
        spec = session.exec(select(ModelSpec).where(ModelSpec.brand == v.brand,
                                                    ModelSpec.model == v.model)).first()
        age = max(1, utcnow().year - (v.year_model or utcnow().year) + 1)
        links = session.exec(select(Listing).where(Listing.vehicle_id == v.id)).all()
        cols.append({
            "vehicle": v,
            "price": v.price, "fipe_price": v.fipe_price, "fipe_diff_pct": v.fipe_diff_pct,
            "km": v.km, "km_year": round(v.km / age) if v.km else None,
            "year_model": v.year_model, "transmission": v.transmission, "color": v.color,
            "city": v.city, "seller_type": v.seller_type, "score": v.score,
            "consumption_city": spec.consumption_city if spec else None,
            "consumption_road": spec.consumption_road if spec else None,
            "red_flags": ", ".join(f["label"] for f in v.red_flags) or "—",
            "links": [(li.source, li.url, li.active) for li in links],
        })
    best = {}
    for r in ROWS:
        vals = [c[r.key] for c in cols if isinstance(c[r.key], int | float)]
        if r.best and len(vals) > 1 and len(set(vals)) > 1:
            best[r.key] = min(vals) if r.best == "min" else max(vals)
    return {"rows": ROWS, "cols": cols, "best": best}


def to_csv(data: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Campo"] + [f"{c['vehicle'].brand} {c['vehicle'].model} {c['vehicle'].version or ''}"
                           .strip() for c in data["cols"]])
    for r in data["rows"]:
        out = []
        for c in data["cols"]:
            val = c[r.key]
            if r.key == "links":
                val = " | ".join(u for _, u, _ in val)
            out.append("" if val is None else val)
        w.writerow([r.label] + out)
    return buf.getvalue()
