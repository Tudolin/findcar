"""Alert rules → Telegram, de-duplicated through alert_log."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import timedelta

from sqlmodel import Session, func, select

from app.core.timeutil import utcnow
from app.models import AlertLog, Listing, PricePoint, Vehicle
from app.services import telegram
from app.services.config_store import get_setting


def brl(v: int | None) -> str:
    return "—" if v is None else f"R$ {v:,.0f}".replace(",", ".")


def vehicle_line(v: Vehicle) -> str:
    fipe = f" · FIPE {v.fipe_diff_pct:+.0f}%" if v.fipe_diff_pct is not None else ""
    km = f" · {v.km:,} km".replace(",", ".") if v.km else ""
    return (f"<b>{html.escape(f'{v.brand} {v.model} {v.version or ""}'.strip())}</b> "
            f"{v.year_model or ''}{km} · {brl(v.price)}{fipe} · score {v.score}")


@dataclass
class RunEvents:
    new_vehicles: set[int] = field(default_factory=set)
    price_drops: list[tuple[int, int, int]] = field(default_factory=list)  # listing, old, new
    inactivated: set[int] = field(default_factory=set)  # vehicle ids


def _emit(session: Session, kind: str, key: str, vehicle_id: int | None, msg: str) -> None:
    if session.exec(select(AlertLog).where(AlertLog.dedupe_key == key)).first():
        return
    sent = telegram.send(msg)
    session.add(AlertLog(kind=kind, dedupe_key=key, vehicle_id=vehicle_id, message=msg, sent=sent))
    session.flush()


def first_url(session: Session, vehicle_id: int) -> str:
    li = session.exec(select(Listing).where(Listing.vehicle_id == vehicle_id)).first()
    return li.url if li else ""


def process(session: Session, ev: RunEvents) -> None:
    cfg = get_setting(session, "alerts")
    for vid in ev.new_vehicles:
        v = session.get(Vehicle, vid)
        if v and v.active and v.score is not None and v.score >= cfg["min_score"]:
            _emit(session, "new", f"new:{vid}", vid,
                  f"🆕 Anúncio novo\n{vehicle_line(v)}\n{first_url(session, vid)}")
    for lid, old, new in ev.price_drops:
        drop = old - new
        pct = drop / old * 100 if old else 0
        if drop <= 0 or not (pct >= cfg["drop_pct"] or drop >= cfg["drop_abs"]):
            continue
        li = session.get(Listing, lid)
        v = session.get(Vehicle, li.vehicle_id) if li else None
        if v:
            _emit(session, "drop", f"drop:{lid}:{new}", v.id,
                  f"📉 Queda de preço: {brl(old)} → {brl(new)} (−{pct:.1f}%)\n"
                  f"{vehicle_line(v)}\n{li.url}")
    if cfg["favorite_inactive"]:
        for vid in ev.inactivated:
            v = session.get(Vehicle, vid)
            if v and v.favorite and not v.active:
                _emit(session, "inactive", f"inactive:{vid}:{utcnow():%Y%m%d}", vid,
                      f"🚫 Favorito saiu do ar (último preço {brl(v.price)})\n{vehicle_line(v)}")


def daily_summary(session: Session) -> str:
    since = utcnow() - timedelta(days=1)
    n_active = session.exec(select(func.count()).select_from(Vehicle).where(Vehicle.active)).one()
    n_new = session.exec(
        select(func.count()).select_from(Vehicle).where(Vehicle.created_at >= since)
    ).one()
    n_changes = session.exec(
        select(func.count()).select_from(PricePoint).where(PricePoint.observed_at >= since)
    ).one()
    top = session.exec(
        select(Vehicle).where(Vehicle.active, Vehicle.stage != "descartado")
        .order_by(Vehicle.score.desc()).limit(5)
    ).all()
    lines = [f"📊 <b>Resumo carwatch</b>\nAtivos: {n_active} · novos 24h: {n_new} · "
             f"mudanças de preço 24h: {n_changes}", "", "<b>Top 5 por score</b>"]
    lines += [f"• {vehicle_line(v)}" for v in top]
    return "\n".join(lines)


def send_daily_summary(session: Session) -> None:
    if not get_setting(session, "alerts")["daily_summary"]:
        return
    _emit(session, "summary", f"summary:{utcnow():%Y%m%d}", None, daily_summary(session))
