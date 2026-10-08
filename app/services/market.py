"""Market context for one vehicle: comparables, price position, time on market, offer range.

All heuristics, shown with their reasons so the user can judge them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from sqlmodel import Session, col, select

from app.core.timeutil import utcnow
from app.models import Vehicle


@dataclass
class Comparables:
    vehicles: list[Vehicle]
    count: int
    median_price: int | None
    median_km: int | None
    cheaper_than_pct: int | None  # % of comparables more expensive than this one
    diff_vs_median: int | None
    diff_vs_median_pct: float | None


@dataclass
class Offer:
    low: int
    high: int
    reasons: list[str] = field(default_factory=list)


def days_listed(v: Vehicle) -> int | None:
    return (utcnow() - v.listed_since).days if v.listed_since else None


def comparables(session: Session, v: Vehicle, limit: int = 6) -> Comparables:
    """Active cars of the same model, ±1 model year, km within ±40% when known."""
    stmt = select(Vehicle).where(
        Vehicle.active, Vehicle.id != v.id, Vehicle.brand == v.brand, Vehicle.model == v.model,
        col(Vehicle.price).is_not(None),
    )
    if v.year_model:
        stmt = stmt.where(Vehicle.year_model.between(v.year_model - 1, v.year_model + 1))
    rows = list(session.exec(stmt).all())
    if v.km:
        rows = [r for r in rows if not r.km or abs(r.km - v.km) <= 0.4 * max(v.km, 20000)]
    if v.transmission:  # an automatic is priced differently; mix only if too few
        same = [r for r in rows if r.transmission == v.transmission]
        if len(same) >= 3:
            rows = same
    prices = [r.price for r in rows]
    med = int(median(prices)) if prices else None
    kms = [r.km for r in rows if r.km]
    cheaper = None
    if prices and v.price:
        cheaper = round(100 * sum(1 for p in prices if p > v.price) / len(prices))
    diff = (v.price - med) if (med and v.price) else None
    rows.sort(key=lambda r: (abs((r.year_model or 0) - (v.year_model or 0)), r.price))
    return Comparables(
        vehicles=rows[:limit], count=len(rows), median_price=med,
        median_km=int(median(kms)) if kms else None, cheaper_than_pct=cheaper,
        diff_vs_median=diff, diff_vs_median_pct=round(100 * diff / med, 1) if diff is not None else None,
    )


def _round(value: float, step: int = 500) -> int:
    return int(round(value / step) * step)


def offer(v: Vehicle, comps: Comparables) -> Offer | None:
    """Opening/target offer. Starts from the asking price and the market references."""
    if not v.price:
        return None
    refs = [v.price]
    reasons = []
    if v.fipe_price:
        refs.append(v.fipe_price)
        reasons.append(f"FIPE {v.fipe_price:,.0f}".replace(",", "."))
    if comps.median_price and comps.count >= 3:
        refs.append(comps.median_price)
        reasons.append(f"mediana de {comps.count} comparáveis {comps.median_price:,.0f}".replace(",", "."))
    anchor = min(refs)
    discount = 0.03  # haggling room most sellers price in
    days = days_listed(v)
    if days is not None and days >= 60:
        discount += 0.04
        reasons.append(f"anunciado há {days} dias (muito tempo parado)")
    elif days is not None and days >= 30:
        discount += 0.02
        reasons.append(f"anunciado há {days} dias")
    if v.price_drop:
        discount += 0.01
        reasons.append("vendedor já baixou o preço (tem pressa)")
    if v.red_flags:
        discount += 0.03
        reasons.append(f"{len(v.red_flags)} red flag(s) a usar na negociação")
    if v.km and v.year_model:
        age = max(1, utcnow().year - v.year_model + 1)
        if v.km / age > 20000:
            discount += 0.02
            reasons.append("km/ano alto")
    high = min(v.price, _round(anchor * (1 - discount / 3)))
    low = _round(anchor * (1 - discount))
    if low >= high:
        low = _round(high * 0.97)
    return Offer(low=low, high=high, reasons=reasons)


CHECKLIST = [
    ("laudo", "Laudo cautelar aprovado"),
    ("debitos", "Débitos, multas e IPVA consultados (Detran-PR)"),
    ("recall", "Recall consultado no site da montadora"),
    ("documento", "CRLV e chassi/motor conferidos com o documento"),
    ("historico", "Histórico de revisões / manual com carimbos"),
    ("test_drive", "Test drive: câmbio, embreagem, freio, direção"),
    ("mecanico", "Avaliação de mecânico de confiança"),
    ("lataria", "Sem sinais de batida, solda ou enchente"),
    ("pneus", "Pneus, freios e suspensão ok"),
    ("ar", "Ar-condicionado, vidros e elétrica funcionando"),
]
