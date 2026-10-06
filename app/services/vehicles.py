"""Vehicle aggregation, merge/split, FIPE + score refresh."""

from __future__ import annotations

from sqlmodel import Session, select

from app.core.timeutil import utcnow
from app.models import KanbanEvent, Listing, ModelSpec, RedFlagRule, Vehicle
from app.services import scoring
from app.services.config_store import get_setting

SOURCE_PRIORITY = {"webmotors": 0, "olx": 1}


def listings_of(session: Session, vehicle_id: int) -> list[Listing]:
    rows = session.exec(select(Listing).where(Listing.vehicle_id == vehicle_id)).all()
    return sorted(rows, key=lambda li: SOURCE_PRIORITY.get(li.source, 9))


def new_vehicle_from(listing: Listing) -> Vehicle:
    return Vehicle(brand=listing.brand or "?", model=listing.model or "?")


def aggregate(session: Session, v: Vehicle) -> None:
    """Recompute denormalized fields from the vehicle's listings."""
    ls = listings_of(session, v.id)
    if not ls:
        return
    active = [li for li in ls if li.active]
    for field in ("brand", "model", "version", "year_fab", "year_model", "km", "color",
                  "transmission", "fuel", "city", "state", "seller_type"):
        value = next((getattr(li, field) for li in ls if getattr(li, field)), None)
        if value is not None:
            setattr(v, field, value)
    if v.km is not None:  # highest km reported is the safest assumption
        v.km = max(li.km for li in ls if li.km) if any(li.km for li in ls) else v.km
    prices = [li.price for li in (active or ls) if li.price]
    v.price = min(prices) if prices else None
    v.active = bool(active)
    if v.fipe_price and v.price:
        v.fipe_diff_pct = round((v.price - v.fipe_price) / v.fipe_price * 100, 1)
    v.updated_at = utcnow()


def rescore(session: Session, v: Vehicle, rules: list[RedFlagRule] | None = None) -> None:
    if rules is None:
        rules = list(session.exec(select(RedFlagRule)).all())
    text = " \n ".join(
        f"{li.title} {li.version or ''} {li.raw_version or ''} {li.description}"
        for li in listings_of(session, v.id)
    )
    spec = session.exec(
        select(ModelSpec).where(ModelSpec.brand == v.brand, ModelSpec.model == v.model)
    ).first()
    res = scoring.compute(
        v, text, rules,
        weights=get_setting(session, "score.weights"),
        seller_values=get_setting(session, "score.seller_values"),
        inferred=get_setting(session, "score.inferred_flags"),
        spec=spec,
    )
    v.score, v.score_breakdown, v.red_flags = res.score, res.breakdown, res.flags


def set_stage(session: Session, v: Vehicle, stage: str) -> None:
    if v.stage == stage:
        return
    session.add(KanbanEvent(vehicle_id=v.id, from_stage=v.stage, to_stage=stage))
    v.stage = stage
    session.add(v)


def merge_listing_into(session: Session, listing: Listing, target: Vehicle) -> None:
    """Manual merge: move listing to `target`, drop the old vehicle if it became empty."""
    old_id = listing.vehicle_id
    listing.vehicle_id, listing.match_manual, listing.match_confidence = target.id, True, 1.0
    session.add(listing)
    session.flush()
    _cleanup(session, old_id, target)
    aggregate(session, target)
    rescore(session, target)
    session.add(target)


def split_listing(session: Session, listing: Listing) -> Vehicle:
    old = session.get(Vehicle, listing.vehicle_id)
    v = new_vehicle_from(listing)
    session.add(v)
    session.flush()
    listing.vehicle_id, listing.match_manual, listing.match_confidence = v.id, True, None
    session.add(listing)
    session.flush()
    for x in (v, old):
        if x:
            aggregate(session, x)
            rescore(session, x)
            session.add(x)
    return v


def _cleanup(session: Session, old_id: int | None, target: Vehicle) -> None:
    if not old_id or old_id == target.id:
        return
    if session.exec(select(Listing).where(Listing.vehicle_id == old_id)).first():
        old = session.get(Vehicle, old_id)
        aggregate(session, old)
        session.add(old)
        return
    old = session.get(Vehicle, old_id)
    if old:
        # keep the user's work: notes/favorite/stage move to the surviving vehicle
        if old.notes and old.notes not in target.notes:
            target.notes = (target.notes + "\n\n" + old.notes).strip()
        target.favorite = target.favorite or old.favorite
        for ev in session.exec(select(KanbanEvent).where(KanbanEvent.vehicle_id == old_id)).all():
            ev.vehicle_id = target.id
            session.add(ev)
        session.delete(old)
