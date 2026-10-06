from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import Session, col, or_, select

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
from app.services import compare, dedupe, runner, stats
from app.services.config_store import get_setting

router = APIRouter()

SORTS = {
    "score": (col(Vehicle.score).desc().nulls_last(),),
    "price": (col(Vehicle.price).asc().nulls_last(),),
    "fipe": (col(Vehicle.fipe_diff_pct).asc().nulls_last(),),
    "km": (col(Vehicle.km).asc().nulls_last(),),
    "year": (col(Vehicle.year_model).desc().nulls_last(),),
    "recent": (col(Vehicle.created_at).desc(),),
}


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


@router.get("/vehicles")
def vehicles(
    request: Request,
    q: str = "",
    model: str = "",
    max_price: int | None = Query(None),
    min_year: int | None = Query(None),
    max_km: int | None = Query(None),
    automatic: bool = False,
    status: str = "active",
    source: str = "",
    stage: str = "",
    sort: str = "score",
    session: Session = Depends(get_session),
):
    stmt = select(Vehicle)
    if status == "active":
        stmt = stmt.where(Vehicle.active)
    elif status == "inactive":
        stmt = stmt.where(Vehicle.active == False)  # noqa: E712
    elif status == "favorites":
        stmt = stmt.where(Vehicle.favorite)
    if model:
        stmt = stmt.where(Vehicle.model == model)
    if max_price:
        stmt = stmt.where(Vehicle.price <= max_price)
    if min_year:
        stmt = stmt.where(Vehicle.year_model >= min_year)
    if max_km:
        stmt = stmt.where(Vehicle.km <= max_km)
    if automatic:
        stmt = stmt.where(Vehicle.transmission == "automatico")
    if stage:
        stmt = stmt.where(Vehicle.stage == stage)
    else:
        stmt = stmt.where(Vehicle.stage != Stage.DESCARTADO)
    if source:
        stmt = stmt.where(col(Vehicle.id).in_(select(Listing.vehicle_id)
                                              .where(Listing.source == source)))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(col(Vehicle.version).ilike(like), col(Vehicle.model).ilike(like),
                              col(Vehicle.notes).ilike(like), col(Vehicle.city).ilike(like)))
    stmt = stmt.order_by(*SORTS.get(sort, SORTS["score"])).limit(300)
    rows = session.exec(stmt).all()
    ids = [v.id for v in rows]
    ctx = {
        "nav": "vehicles",
        "vehicles": rows,
        "photos": _photos(session, ids),
        "sources": _sources_of(session, ids),
        "models": stats.models_present(session),
        "f": {"q": q, "model": model, "max_price": max_price, "min_year": min_year,
              "max_km": max_km, "automatic": automatic, "status": status, "source": source,
              "stage": stage, "sort": sort},
    }
    tpl = "partials/vehicle_grid.html" if _is_htmx(request) else "vehicles.html"
    return templates.TemplateResponse(request, tpl, ctx)


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
    return templates.TemplateResponse(request, "vehicle.html", {
        "nav": "vehicles", "v": v, "listings": listings, "history": history,
        "suggestions": suggestions, "photos": photos, "events": events, "spec": spec,
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
        "flags": session.exec(select(RedFlagRule).order_by(RedFlagRule.id)).all(),
        "aliases": session.exec(select(Alias).order_by(Alias.kind, Alias.canonical)).all(),
        "specs": session.exec(select(ModelSpec).order_by(ModelSpec.brand, ModelSpec.model)).all(),
        "sources": session.exec(select(SourceStatus).order_by(SourceStatus.name)).all(),
        "telegram": telegram.configured(),
    })
