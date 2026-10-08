"""Mutating endpoints. Classic form posts redirect (303); HTMX calls get partials/toasts."""

import json
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.api.templating import templates
from app.core.db import get_session
from app.models import (
    Alias,
    Listing,
    ModelSpec,
    RedFlagRule,
    SavedSearch,
    SourceStatus,
    Stage,
    Vehicle,
)
from app.services import runner, scheduler, telegram
from app.services.config_store import get_setting, set_setting
from app.services.fipe import FipeService
from app.services.vehicles import merge_listing_into, rescore, set_stage, split_listing, toggle_favorite

router = APIRouter()


def back(url: str, msg: str | None = None) -> RedirectResponse:
    if msg:
        url += ("&" if "?" in url else "?") + "ok=" + quote(msg)
    return RedirectResponse(url, status_code=303)


def toast(resp: Response, msg: str, kind: str = "ok") -> Response:
    resp.headers["HX-Trigger"] = json.dumps({"toast": {"msg": msg, "kind": kind}})
    return resp


def _vehicle(session: Session, vid: int) -> Vehicle:
    v = session.get(Vehicle, vid)
    if not v:
        raise HTTPException(404)
    return v


# -- vehicles -------------------------------------------------------------------------
@router.post("/vehicles/{vid}/favorite")
def favorite_endpoint(vid: int, request: Request, session: Session = Depends(get_session)):
    v = _vehicle(session, vid)
    toggle_favorite(v)
    session.add(v)
    session.commit()
    return templates.TemplateResponse(request, "partials/fav.html", {"v": v})


@router.post("/vehicles/{vid}/stage")
def change_stage(vid: int, stage: str = Form(...), session: Session = Depends(get_session)):
    if stage not in {s.value for s in Stage}:
        raise HTTPException(400)
    v = _vehicle(session, vid)
    set_stage(session, v, stage)
    session.commit()
    return toast(Response(status_code=204), "Etapa atualizada")


@router.post("/vehicles/{vid}/checklist/{key}")
def toggle_checklist(vid: int, key: str, request: Request, session: Session = Depends(get_session)):
    from app.services.market import CHECKLIST

    if key not in dict(CHECKLIST):
        raise HTTPException(400)
    v = _vehicle(session, vid)
    v.checklist = {**(v.checklist or {}), key: not (v.checklist or {}).get(key, False)}
    session.add(v)
    session.commit()
    return templates.TemplateResponse(request, "partials/checklist.html", {"v": v, "checklist": CHECKLIST})


@router.post("/vehicles/{vid}/notes")
def save_notes(vid: int, notes: str = Form(""), session: Session = Depends(get_session)):
    v = _vehicle(session, vid)
    v.notes = notes
    session.add(v)
    session.commit()
    return toast(Response(status_code=204), "Notas salvas")


@router.post("/vehicles/{vid}/refresh")
def refresh_vehicle(vid: int, session: Session = Depends(get_session)):
    from app.core.timeutil import utcnow

    v = _vehicle(session, vid)
    fipe = FipeService(session, price_ttl_days=0)
    try:
        found = fipe.update_vehicle(v)
    finally:
        fipe.client.close()
    v.fipe_checked_at = utcnow()
    rescore(session, v)
    session.add(v)
    session.commit()
    return back(f"/vehicles/{vid}", "FIPE e score atualizados" if found
                else "Score atualizado (FIPE não encontrada)")


@router.post("/vehicles/{vid}/merge")
def merge_vehicle(vid: int, other_id: int = Form(...), session: Session = Depends(get_session)):
    target, other = _vehicle(session, vid), _vehicle(session, other_id)
    for li in session.exec(select(Listing).where(Listing.vehicle_id == other.id)).all():
        merge_listing_into(session, li, target)
    session.commit()
    return back(f"/vehicles/{vid}", "Veículos unidos")


@router.post("/listings/{lid}/split")
def split(lid: int, session: Session = Depends(get_session)):
    li = session.get(Listing, lid)
    if not li:
        raise HTTPException(404)
    v = split_listing(session, li)
    session.commit()
    return back(f"/vehicles/{v.id}", "Anúncio separado em um novo veículo")


# -- saved searches -------------------------------------------------------------------
def _parse_search_form(form) -> tuple[str, dict, list[str], bool]:
    models = []
    for line in (form.get("models") or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            models.append({"brand": parts[0].strip(), "model": parts[1].strip()})
    cities = [c.strip() for c in re.split(r"[\n;]", form.get("cities") or "") if c.strip()]

    def opt_int(name):
        v = (form.get(name) or "").strip().replace(".", "")
        return int(v) if v.isdigit() else None

    filters = {
        "models": models,
        "max_price": opt_int("max_price"),
        "min_year": opt_int("min_year"),
        "max_km": opt_int("max_km"),
        "automatic_only": form.get("automatic_only") == "on",
        "state": (form.get("state") or "pr").lower(),
        "city": (form.get("city") or "curitiba").lower(),
        "cities": cities,
        "max_pages": opt_int("max_pages") or 3,
    }
    sources = form.getlist("sources") or ["webmotors"]
    return form.get("name") or "Busca", filters, sources, form.get("enabled") == "on"


@router.post("/searches")
async def create_search(request: Request, session: Session = Depends(get_session)):
    name, filters, sources, enabled = _parse_search_form(await request.form())
    session.add(SavedSearch(name=name, filters=filters, sources=sources, enabled=enabled))
    session.commit()
    return back("/searches", "Busca criada")


@router.post("/searches/{sid}")
async def update_search(sid: int, request: Request, session: Session = Depends(get_session)):
    s = session.get(SavedSearch, sid)
    if not s:
        raise HTTPException(404)
    s.name, s.filters, s.sources, s.enabled = _parse_search_form(await request.form())
    session.add(s)
    session.commit()
    return back("/searches", "Busca salva")


@router.post("/searches/{sid}/delete")
def delete_search(sid: int, session: Session = Depends(get_session)):
    from app.models import SearchHit, SearchRun

    s = session.get(SavedSearch, sid)
    if s:
        for h in session.exec(select(SearchHit).where(SearchHit.saved_search_id == sid)).all():
            session.delete(h)
        for r in session.exec(select(SearchRun).where(SearchRun.saved_search_id == sid)).all():
            r.saved_search_id = None
            session.add(r)
        session.delete(s)
        session.commit()
    return back("/searches", "Busca removida")


@router.post("/searches/{sid}/run")
def run_search_now(sid: int):
    started = runner.run_in_background(sid)
    return back("/searches", "Execução iniciada — acompanhe em Saúde" if started
                else "Já existe uma execução em andamento")


@router.post("/run-all")
def run_all_now():
    started = runner.run_in_background()
    return back("/health", "Execução iniciada" if started else "Já existe uma execução em andamento")


@router.post("/sources/{name}/toggle")
def toggle_source(name: str, session: Session = Depends(get_session)):
    st = session.get(SourceStatus, name) or SourceStatus(name=name)
    st.enabled = not st.enabled
    session.add(st)
    session.commit()
    return back("/settings#fontes", f"{name} {'ativada' if st.enabled else 'desativada'}")


# -- settings ---------------------------------------------------------------------------
def _num(form, key, default, cast=float):
    try:
        return cast(str(form.get(key, default)).replace(",", "."))
    except ValueError:
        return default


@router.post("/settings/score")
async def save_score(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    weights = get_setting(session, "score.weights")
    set_setting(session, "score.weights",
                {k: _num(form, f"w_{k}", v, int) for k, v in weights.items()})
    set_setting(session, "score.seller_values", {
        "particular": _num(form, "seller_particular", 1.0),
        "loja": _num(form, "seller_loja", 0.8)})
    set_setting(session, "score.inferred_flags", {
        "powershift": _num(form, "inf_powershift", 15, int),
        "al4": _num(form, "inf_al4", 15, int),
        "too_cheap": _num(form, "inf_too_cheap", 25, int),
        "too_cheap_pct": _num(form, "inf_too_cheap_pct", -35, int)})
    session.commit()
    _rescore_all(session)
    return back("/settings#score", "Pesos salvos e scores recalculados")


def _rescore_all(session: Session) -> None:
    rules = list(session.exec(select(RedFlagRule)).all())
    for v in session.exec(select(Vehicle)).all():
        rescore(session, v, rules)
        session.add(v)
    session.commit()


@router.post("/settings/alerts")
async def save_alerts(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    set_setting(session, "alerts", {
        "min_score": _num(form, "min_score", 70, int),
        "drop_pct": _num(form, "drop_pct", 5.0),
        "drop_abs": _num(form, "drop_abs", 1500, int),
        "favorite_inactive": form.get("favorite_inactive") == "on",
        "daily_summary": form.get("daily_summary") == "on",
        "daily_summary_time": form.get("daily_summary_time") or "20:00",
    })
    session.commit()
    scheduler.reload()
    return back("/settings#alertas", "Alertas salvos")


@router.post("/settings/finance")
async def save_finance(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    months = _num(form, "months", 48, int)
    set_setting(session, "finance", {
        "rate_month": max(0.0, min(15.0, _num(form, "rate_month", 1.99))),
        "down_pct": max(0, min(100, _num(form, "down_pct", 30, int))),
        "months": months if months in (12, 24, 36, 48, 60) else 48,
        "iof": form.get("iof") == "on",
        "fees": max(0, _num(form, "fees", 0, int)),
    })
    session.commit()
    return back("/settings#financiamento", "Padrões de financiamento salvos")


@router.post("/settings/schedule")
async def save_schedule(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    times = [t for t in re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", form.get("times") or "")]
    times = sorted({f"{int(h):02d}:{m}" for h, m in times}) or ["08:10"]
    set_setting(session, "schedule", {"times": times[:6],
                                      "fipe_refresh_days": _num(form, "fipe_refresh_days", 15, int)})
    set_setting(session, "inactive_after_runs", max(1, _num(form, "inactive_after", 3, int)))
    set_setting(session, "dedupe", {
        "auto_threshold": _num(form, "auto_threshold", 0.75),
        "suggest_threshold": _num(form, "suggest_threshold", 0.5),
        "photo_hash": form.get("photo_hash") == "on"})
    set_setting(session, "details", {"max_per_run": _num(form, "max_details", 25, int)})
    session.commit()
    scheduler.reload()
    return back("/settings#agenda", "Agenda salva")


@router.post("/settings/flags")
def add_flag(label: str = Form(...), pattern: str = Form(...), penalty: int = Form(10),
             session: Session = Depends(get_session)):
    try:
        re.compile(pattern)
    except re.error:
        return back("/settings#flags", "Regex inválida")
    session.add(RedFlagRule(label=label, pattern=pattern, penalty=penalty))
    session.commit()
    _rescore_all(session)
    return back("/settings#flags", "Red flag adicionada")


@router.post("/settings/flags/{fid}/toggle")
def toggle_flag(fid: int, session: Session = Depends(get_session)):
    r = session.get(RedFlagRule, fid)
    if r:
        r.enabled = not r.enabled
        session.add(r)
        session.commit()
        _rescore_all(session)
    return back("/settings#flags")


@router.post("/settings/flags/{fid}/delete")
def delete_flag(fid: int, session: Session = Depends(get_session)):
    r = session.get(RedFlagRule, fid)
    if r:
        session.delete(r)
        session.commit()
        _rescore_all(session)
    return back("/settings#flags", "Red flag removida")


@router.post("/settings/aliases")
def add_alias(kind: str = Form(...), pattern: str = Form(...), canonical: str = Form(...),
              brand: str = Form(""), session: Session = Depends(get_session)):
    from app.services.normalize import norm

    if kind not in {"brand", "model", "version"}:
        raise HTTPException(400)
    session.add(Alias(kind=kind, pattern=norm(pattern), canonical=canonical.strip(),
                      brand=brand.strip() or None))
    session.commit()
    return back("/settings#aliases", "Alias adicionado (vale para as próximas execuções)")


@router.post("/settings/aliases/{aid}/delete")
def delete_alias(aid: int, session: Session = Depends(get_session)):
    a = session.get(Alias, aid)
    if a:
        session.delete(a)
        session.commit()
    return back("/settings#aliases", "Alias removido")


@router.post("/settings/specs")
async def save_specs(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    for spec in session.exec(select(ModelSpec)).all():
        spec.consumption_city = _num(form, f"city_{spec.id}", spec.consumption_city) or None
        spec.consumption_road = _num(form, f"road_{spec.id}", spec.consumption_road) or None
        session.add(spec)
    if form.get("new_brand") and form.get("new_model"):
        session.add(ModelSpec(brand=form["new_brand"].strip(), model=form["new_model"].strip(),
                              consumption_city=_num(form, "new_city", 0) or None,
                              consumption_road=_num(form, "new_road", 0) or None))
    session.commit()
    _rescore_all(session)
    return back("/settings#consumo", "Consumo salvo")


@router.post("/settings/telegram-test")
def telegram_test():
    ok = telegram.send("✅ carwatch: teste de notificação")
    return back("/settings#alertas", "Mensagem enviada" if ok
                else "Falhou — confira TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID")
