from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import Session, col, func, select

from app.api.templating import templates
from app.core.db import get_session
from app.models import (
    Alias,
    KanbanEvent,
    Listing,
    ModelSpec,
    PricePoint,
    RedFlagRule,
    SavedSearch,
    SourceStatus,
    Stage,
    Vehicle,
)
from app.services import compare, dedupe, finance, market, runner, stats
from app.services.config_store import get_setting
from app.services.vehicle_filters import PAGE_SIZE, SORTS, STATUSES, VehicleFilter

router = APIRouter()

def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@router.get("/")
def dashboard(request: Request, session: Session = Depends(get_session)):
    top = session.exec(
        select(Vehicle).where(Vehicle.active, Vehicle.stage != Stage.DESCARTADO)
        .order_by(col(Vehicle.score).desc().nulls_last()).limit(6)
    ).all()
    return templates.TemplateResponse(request, "dashboard.html", {
        "nav": "dashboard",
        "kpis": stats.kpis(session),
        "median": stats.median_price_series(session),
        "kmh": stats.km_histogram(session),
        "by_source": stats.active_by_source(session),
        "models": stats.models_present(session),
        "top": top,
        "photos": _photos(session, [v.id for v in top]),
        "runs": stats.recent_runs(session, 6),
        "running": runner.is_running(),
        "drops": session.exec(
            select(Vehicle).where(Vehicle.active, Vehicle.price_drop > 0)
            .order_by(col(Vehicle.price_drop_at).desc().nulls_last()).limit(6)
        ).all(),
    })


@router.get("/api/median")
def median_api(model: str | None = None, session: Session = Depends(get_session)):
    return stats.median_price_series(session, model=model or None)


def _photos(session: Session, ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    if not ids:
        return out
    for li in session.exec(select(Listing).where(col(Listing.vehicle_id).in_(ids))).all():
        if li.photos and li.vehicle_id not in out:
            out[li.vehicle_id] = li.photos[0]
    return out


def _sources_of(session: Session, ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    if not ids:
        return out
    for vid, src in session.exec(
        select(Listing.vehicle_id, Listing.source).where(col(Listing.vehicle_id).in_(ids))
    ).all():
        out.setdefault(vid, [])
        if src not in out[vid]:
            out[vid].append(src)
    return out


def _cities(session: Session) -> list[str]:
    return sorted({c for c in session.exec(select(Vehicle.city).where(Vehicle.active)).all() if c})


@router.get("/vehicles")
def vehicles(request: Request, session: Session = Depends(get_session)):
    f = VehicleFilter.from_params(request.query_params)
    base = f.statement()
    total = session.exec(select(func.count()).select_from(base.subquery())).one()
    rows = session.exec(f.ordered(base).offset(f.offset).limit(PAGE_SIZE)).all()
    ids = [v.id for v in rows]
    ctx = {
        "nav": "favorites" if f.status == "favorites" else "vehicles",
        "vehicles": rows,
        "total": total,
        "next_offset": f.offset + len(rows) if f.offset + len(rows) < total else None,
        "photos": _photos(session, ids),
        "sources": _sources_of(session, ids),
        "models": stats.models_present(session),
        "cities": _cities(session),
        "f": f,
        "sorts": {k: v[0] for k, v in SORTS.items()},
        "statuses": STATUSES,
    }
    if request.headers.get("HX-Request") == "true":
        # "Carregar mais" appends cards; a filter change replaces the whole result block.
        tpl = "partials/vehicle_more.html" if f.offset else "partials/vehicle_grid.html"
        return templates.TemplateResponse(request, tpl, ctx)
    return templates.TemplateResponse(request, "vehicles.html", ctx)


@router.get("/favorites")
def favorites(request: Request, session: Session = Depends(get_session)):
    rows = session.exec(
        select(Vehicle).where(Vehicle.favorite)
        .order_by(col(Vehicle.active).desc(), col(Vehicle.score).desc().nulls_last())
    ).all()
    ids = [v.id for v in rows]
    summary = {
        "count": len(rows),
        "active": sum(1 for v in rows if v.active),
        "dropped": sum(1 for v in rows if v.favorite_price and v.price and v.price < v.favorite_price),
        "saved": sum(v.favorite_price - v.price for v in rows
                     if v.favorite_price and v.price and v.price < v.favorite_price),
    }
    return templates.TemplateResponse(request, "favorites.html", {
        "nav": "favorites", "vehicles": rows, "photos": _photos(session, ids),
        "sources": _sources_of(session, ids), "summary": summary,
        "days": {v.id: market.days_listed(v) for v in rows},
    })


@router.get("/vehicles/{vid}")
def vehicle_detail(vid: int, request: Request, session: Session = Depends(get_session)):
    v = session.get(Vehicle, vid)
    if not v:
        raise HTTPException(404)
    listings = session.exec(select(Listing).where(Listing.vehicle_id == vid)).all()
    history = {}
    for li in listings:
        pts = session.exec(select(PricePoint).where(PricePoint.listing_id == li.id)
                           .order_by(PricePoint.observed_at)).all()
        history[li.source] = [{"x": p.observed_at.isoformat(), "y": p.price} for p in pts]
        if pts and li.last_seen > pts[-1].observed_at:
            history[li.source].append({"x": li.last_seen.isoformat(), "y": pts[-1].price})
    cfg = get_setting(session, "dedupe")
    suggestions = []
    seen = set()
    for li in listings:
        for other, ms in dedupe.candidates(session, li):
            if other.id not in seen and ms.score >= cfg["suggest_threshold"]:
                seen.add(other.id)
                suggestions.append((other, ms))
    photos = [p for li in listings for p in li.photos][:24]
    events = session.exec(select(KanbanEvent).where(KanbanEvent.vehicle_id == vid)
                          .order_by(col(KanbanEvent.at).desc())).all()
    spec = session.exec(select(ModelSpec).where(ModelSpec.brand == v.brand,
                                                ModelSpec.model == v.model)).first()
    comps = market.comparables(session, v)
    phone = next((li.seller_phone for li in listings if li.seller_phone and li.active), None)
    return templates.TemplateResponse(request, "vehicle.html", {
        "nav": "vehicles", "v": v, "listings": listings, "history": history,
        "suggestions": suggestions, "photos": photos, "events": events, "spec": spec,
        "comps": comps, "offer": market.offer(v, comps), "days": market.days_listed(v),
        "checklist": market.CHECKLIST, "phone": phone,
        "comp_photos": _photos(session, [c.id for c in comps.vehicles]),
        "fin": get_setting(session, "finance"),
        "fin_table": finance.table(v.price, get_setting(session, "finance")) if v.price else [],
    })


def _ids(ids: str) -> list[int]:
    return [int(x) for x in ids.split(",") if x.strip().isdigit()]


@router.get("/compare")
def compare_page(request: Request, ids: str = "", session: Session = Depends(get_session)):
    vs = compare.load(session, _ids(ids))
    return templates.TemplateResponse(request, "compare.html", {
        "nav": "compare", "data": compare.table(session, vs), "ids": ids,
        "photos": _photos(session, [v.id for v in vs]),
    })


@router.get("/compare.csv")
def compare_csv(ids: str = "", session: Session = Depends(get_session)):
    data = compare.table(session, compare.load(session, _ids(ids)))
    return PlainTextResponse(
        "﻿" + compare.to_csv(data), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="carwatch-comparativo.csv"'},
    )


@router.get("/kanban")
def kanban(request: Request, session: Session = Depends(get_session)):
    vs = session.exec(select(Vehicle).order_by(col(Vehicle.score).desc().nulls_last())).all()
    cols = {s: [] for s in Stage}
    for v in vs:
        # "Novo" only shows cars you haven't triaged that are still active and decent
        if v.stage == Stage.NOVO and not v.favorite and (not v.active or (v.score or 0) < 50):
            continue
        cols[Stage(v.stage)].append(v)
    hidden = max(0, len(cols[Stage.NOVO]) - 40)
    cols[Stage.NOVO] = cols[Stage.NOVO][:40]
    return templates.TemplateResponse(request, "kanban.html", {
        "nav": "kanban", "cols": cols, "hidden_new": hidden, "photos": _photos(session, [v.id for v in vs]),
    })


@router.get("/searches")
def searches(request: Request, session: Session = Depends(get_session)):
    rows = session.exec(select(SavedSearch).order_by(SavedSearch.id)).all()
    return templates.TemplateResponse(request, "searches.html", {
        "nav": "searches", "searches": rows, "running": runner.is_running(),
        "source_status": {s.name: s for s in session.exec(select(SourceStatus)).all()},
    })


@router.get("/searches/new")
@router.get("/searches/{sid}/edit")
def search_form(request: Request, sid: int | None = None, session: Session = Depends(get_session)):
    from app.seed import RMC_CURITIBA

    s = session.get(SavedSearch, sid) if sid else None
    if sid and not s:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "search_form.html", {
        "nav": "searches", "s": s, "rmc": RMC_CURITIBA,
    })


@router.get("/settings")
def settings_page(request: Request, session: Session = Depends(get_session)):
    from app.services import telegram

    return templates.TemplateResponse(request, "settings.html", {
        "nav": "settings",
        "weights": get_setting(session, "score.weights"),
        "seller_values": get_setting(session, "score.seller_values"),
        "inferred": get_setting(session, "score.inferred_flags"),
        "alerts": get_setting(session, "alerts"),
        "schedule": get_setting(session, "schedule"),
        "dedupe": get_setting(session, "dedupe"),
        "inactive_after": get_setting(session, "inactive_after_runs"),
        "details": get_setting(session, "details"),
        "fin": get_setting(session, "finance"),
        "flags": session.exec(select(RedFlagRule).order_by(RedFlagRule.id)).all(),
        "aliases": session.exec(select(Alias).order_by(Alias.kind, Alias.canonical)).all(),
        "specs": session.exec(select(ModelSpec).order_by(ModelSpec.brand, ModelSpec.model)).all(),
        "sources": session.exec(select(SourceStatus).order_by(SourceStatus.name)).all(),
        "telegram": telegram.configured(),
    })
