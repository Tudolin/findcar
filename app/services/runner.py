"""Run saved searches against their sources (called by the scheduler or the UI)."""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from sqlmodel import Session, select

from app.adapters import ADAPTERS, SearchFilters
from app.core.db import session_scope
from app.core.http import BlockedError, PoliteClient
from app.core.timeutil import utcnow
from app.models import Listing, RedFlagRule, SavedSearch, SearchRun, SourceStatus, Vehicle
from app.services import alerts, dedupe
from app.services.config_store import get_setting
from app.services.fipe import FipeService
from app.services.ingest import (
    IngestStats,
    assign_vehicle,
    mark_missing,
    refresh_vehicles,
    upsert,
)
from app.services.normalize import Normalizer
from app.services.vehicles import rescore

log = logging.getLogger(__name__)
_run_lock = threading.Lock()


def is_running() -> bool:
    return _run_lock.locked()


def filters_for(search: SavedSearch) -> list[SearchFilters]:
    f = search.filters or {}
    return [
        SearchFilters(
            brand=m["brand"], model=m["model"],
            max_price=f.get("max_price"), min_year=f.get("min_year"), max_km=f.get("max_km"),
            automatic_only=bool(f.get("automatic_only")),
            state=f.get("state", "pr"), city=f.get("city", "curitiba"),
            cities=f.get("cities") or [], max_pages=int(f.get("max_pages", 3)),
        )
        for m in f.get("models", [])
    ]


def _status(session: Session, name: str) -> SourceStatus:
    st = session.get(SourceStatus, name)
    if st is None:
        st = SourceStatus(name=name)
        session.add(st)
        session.flush()
    return st


def run_search_source(session: Session, search: SavedSearch, source: str,
                      adapter=None) -> SearchRun:
    run = SearchRun(saved_search_id=search.id, source=source)
    session.add(run)
    session.flush()
    st = _status(session, source)
    st.last_run_at = utcnow()
    adapter = adapter or ADAPTERS[source]()
    stats, events = IngestStats(), alerts.RunEvents()
    normalizer = Normalizer.from_db(session)
    seen: set[int] = set()
    new_listings: list[Listing] = []
    ok = True
    try:
        for flt in filters_for(search):
            for raw in adapter.search(flt):
                before = stats.new
                li = upsert(session, raw, normalizer, search.id, stats, events)
                seen.add(li.id)
                if stats.new > before:
                    new_listings.append(li)
        _enrich_new(session, adapter, new_listings)
        for li in [session.get(Listing, i) for i in seen]:
            v = assign_vehicle(session, li, events)
            stats.touched_vehicles.add(v.id)
        mark_missing(session, search.id, source, seen, stats, events)
        run.status = "ok"
        st.last_status, st.last_error, st.blocked_since = "ok", None, None
        st.last_success_at = utcnow()
    except BlockedError as exc:
        ok = False
        run.status, run.error = "blocked", str(exc)
        st.last_status, st.last_error = "blocked", str(exc)
        st.blocked_since = st.blocked_since or utcnow()
        log.warning("source blocked; run stopped", extra={"source": source, "err": str(exc)})
        alerts._emit(session, "blocked", f"blocked:{source}:{utcnow():%Y%m%d}", None,
                     f"⛔ {source} bloqueou o acesso (anti-bot). Execução interrompida; "
                     "nenhuma tentativa de contornar.")
    except Exception as exc:
        ok = False
        run.status, run.error = "error", f"{type(exc).__name__}: {exc}"[:2000]
        st.last_status, st.last_error = "error", run.error
        log.exception("source run failed", extra={"source": source})

    if ok:
        refresh_vehicles(session, stats.touched_vehicles)
        _fipe_and_score(session, stats.touched_vehicles)
        alerts.process(session, events)
    run.n_found, run.n_new = stats.found, stats.new
    run.n_price_changes, run.n_inactivated = stats.price_changes, stats.inactivated
    run.finished_at = utcnow()
    st.last_count = stats.found
    session.add_all([run, st])
    return run


def _enrich_new(session: Session, adapter, listings: list[Listing]) -> None:
    """Detail pages + photo hashes for brand-new listings only (bounded per run)."""
    cap = int(get_setting(session, "details")["max_per_run"])
    use_hash = get_setting(session, "dedupe")["photo_hash"]
    img_client = PoliteClient(min_delay=1.0, max_delay=2.0, cache_ttl=0) if use_hash else None
    try:
        for li in listings[:cap]:
            if adapter.supports_detail and not li.detail_fetched:
                detail = adapter.fetch_detail(li.url)
                if detail and detail.description:
                    li.description = detail.description
                li.detail_fetched = True
            if img_client and li.photos and not li.photo_hash:
                li.photo_hash = dedupe.photo_hash_for(img_client, li.photos[0])
            session.add(li)
    finally:
        if img_client:
            img_client.close()


def _fipe_and_score(session: Session, vehicle_ids: set[int]) -> None:
    rules = list(session.exec(select(RedFlagRule)).all())
    days = int(get_setting(session, "schedule").get("fipe_refresh_days", 15))
    fipe = FipeService(session, price_ttl_days=days)
    try:
        for vid in vehicle_ids:
            v = session.get(Vehicle, vid)
            if not v:
                continue
            if v.fipe_checked_at is None or utcnow() - v.fipe_checked_at > timedelta(days=days):
                fipe.update_vehicle(v)
                v.fipe_checked_at = utcnow()
            rescore(session, v, rules)
            session.add(v)
    finally:
        fipe.client.close()


def run_search(search_id: int) -> list[dict]:
    """Run one saved search on all its enabled sources. Serialized by a process-wide lock."""
    if not _run_lock.acquire(blocking=False):
        log.info("run skipped: another run in progress")
        return []
    try:
        results = []
        with session_scope() as session:
            search = session.get(SavedSearch, search_id)
            if not search or not search.enabled:
                return []
            sources = search.sources or list(ADAPTERS)
        for source in sources:
            with session_scope() as session:
                st = session.get(SourceStatus, source)
                if st is not None and not st.enabled:
                    continue
                search = session.get(SavedSearch, search_id)
                run = run_search_source(session, search, source)
                search.last_run_at = utcnow()
                session.add(search)
                results.append({"source": source, "status": run.status, "found": run.n_found,
                                "new": run.n_new})
                log.info("run finished", extra={"search": search_id, **results[-1]})
        return results
    finally:
        _run_lock.release()


def run_all() -> None:
    with session_scope() as session:
        ids = [s.id for s in session.exec(select(SavedSearch).where(SavedSearch.enabled)).all()]
    for sid in ids:
        run_search(sid)


def run_in_background(search_id: int | None = None) -> bool:
    if is_running():
        return False
    target = (lambda: run_search(search_id)) if search_id else run_all
    threading.Thread(target=target, name="carwatch-run", daemon=True).start()
    return True
