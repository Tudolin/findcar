"""Aggregations for the dashboard (done in Python: data volume is small)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median

from sqlmodel import Session, func, select

from app.core.timeutil import utcnow
from app.models import Listing, PricePoint, SearchRun, Vehicle


def kpis(session: Session) -> dict:
    now = utcnow()
    count = lambda q: session.exec(q).one()  # noqa: E731
    drops = 0
    week = now - timedelta(days=7)
    for li in session.exec(select(Listing).where(Listing.last_price.is_not(None))).all():
        if li.last_price and li.price and li.price < li.last_price and li.last_seen >= week:
            drops += 1
    return {
        "active": count(select(func.count()).select_from(Vehicle).where(Vehicle.active)),
        "new_24h": count(select(func.count()).select_from(Vehicle)
                         .where(Vehicle.created_at >= now - timedelta(days=1))),
        "drops_7d": drops,
        "favorites": count(select(func.count()).select_from(Vehicle).where(Vehicle.favorite)),
        "below_fipe": count(select(func.count()).select_from(Vehicle)
                            .where(Vehicle.active, Vehicle.fipe_diff_pct < 0)),
    }


def median_price_series(session: Session, weeks: int = 16, model: str | None = None) -> dict:
    """Weekly median asking price. Without `model`: one line per model; with it: per year."""
    start = utcnow() - timedelta(weeks=weeks)
    q = (select(PricePoint.price, PricePoint.observed_at, Listing.id, Listing.model,
                Listing.year_model)
         .join(Listing, Listing.id == PricePoint.listing_id))
    if model:
        q = q.where(Listing.model == model)
    rows = session.exec(q).all()
    # price in effect for each listing at the end of each week
    by_listing: dict[int, list] = defaultdict(list)
    meta = {}
    for price, at, lid, mdl, year in rows:
        by_listing[lid].append((at, price))
        meta[lid] = str(year) if model else mdl
    last_seen = dict(session.exec(select(Listing.id, Listing.last_seen)).all())
    labels, series = [], defaultdict(list)
    week_ends = [start + timedelta(weeks=i + 1) for i in range(weeks)]
    buckets: dict[str, list[list[int]]] = defaultdict(lambda: [[] for _ in week_ends])
    for lid, pts in by_listing.items():
        pts.sort()
        for wi, end in enumerate(week_ends):
            if pts[0][0] > end or last_seen.get(lid, end) < end - timedelta(weeks=1):
                continue
            price = [p for t, p in pts if t <= end][-1]
            buckets[meta[lid]][wi].append(price)
    labels = [e.strftime("%d/%m") for e in week_ends]
    for key, weekly in sorted(buckets.items()):
        series[key] = [round(median(w)) if w else None for w in weekly]
    return {"labels": labels, "series": dict(series)}


def km_histogram(session: Session, step: int = 20000, top: int = 200000) -> dict:
    kms = session.exec(select(Vehicle.km).where(Vehicle.active, Vehicle.km.is_not(None))).all()
    edges = list(range(0, top, step))
    counts = [0] * (len(edges) + 1)
    for km in kms:
        counts[min(km // step, len(edges))] += 1
    labels = [f"{e // 1000}–{(e + step) // 1000}k" for e in edges] + [f"{top // 1000}k+"]
    return {"labels": labels, "counts": counts}


def active_by_source(session: Session) -> dict:
    c = Counter(session.exec(select(Listing.source).where(Listing.active)).all())
    return {"labels": list(c.keys()), "counts": list(c.values())}


def recent_runs(session: Session, limit: int = 12) -> list[SearchRun]:
    return list(session.exec(select(SearchRun).order_by(SearchRun.started_at.desc())
                             .limit(limit)).all())


def models_present(session: Session) -> list[str]:
    return sorted({m for m in session.exec(select(Vehicle.model)).all() if m})
