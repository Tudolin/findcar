"""Persist adapter results: upsert listings, price history, sold detection, vehicles."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.adapters.base import RawListing
from app.core.timeutil import utcnow
from app.models import Listing, PricePoint, SearchHit, Vehicle
from app.services import dedupe
from app.services.alerts import RunEvents
from app.services.config_store import get_setting
from app.services.normalize import Normalizer, normalize_seller, normalize_transmission
from app.services.vehicles import aggregate, new_vehicle_from

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    found: int = 0
    new: int = 0
    price_changes: int = 0
    inactivated: int = 0
    touched_vehicles: set[int] = field(default_factory=set)


def apply_raw(listing: Listing, raw: RawListing, normalizer: Normalizer) -> None:
    canon = normalizer.resolve(raw.brand, raw.model, raw.version, raw.title)
    listing.url = raw.url or listing.url
    listing.title = raw.title or listing.title
    listing.raw_brand, listing.raw_model, listing.raw_version = raw.brand, raw.model, raw.version
    listing.brand, listing.model, listing.version = canon.brand, canon.model, canon.version
    for f in ("year_fab", "year_model", "km", "fuel", "color", "city", "neighborhood", "state",
              "seller_name", "seller_phone", "published_at"):
        value = getattr(raw, f)
        if value is not None:
            setattr(listing, f, value)
    listing.transmission = (
        normalize_transmission(raw.transmission)
        or normalize_transmission(raw.version)
        or normalize_transmission(raw.title)
        or listing.transmission
    )
    listing.seller_type = normalize_seller(raw.seller_type) or listing.seller_type
    if raw.photos:
        listing.photos = raw.photos
    if raw.description:
        listing.description = raw.description
    if raw.has_detail:
        listing.detail_fetched = True


def merge_detail(listing: Listing, detail: RawListing) -> None:
    """Fill a listing with what only the ad page has (description, color, seller, photos…)."""
    if detail.description:
        listing.description = detail.description
    for f in ("color", "fuel", "seller_name", "seller_phone", "neighborhood"):
        value = getattr(detail, f)
        if value and not getattr(listing, f):
            setattr(listing, f, value)
    if detail.km and not listing.km:
        listing.km = detail.km
    if detail.year_fab and detail.year_model and detail.year_fab != detail.year_model:
        listing.year_fab, listing.year_model = detail.year_fab, detail.year_model
    listing.transmission = normalize_transmission(detail.transmission) or listing.transmission
    listing.seller_type = normalize_seller(detail.seller_type) or listing.seller_type
    if len(detail.photos) > len(listing.photos or []):
        listing.photos = detail.photos
    listing.detail_fetched = True


def upsert(session: Session, raw: RawListing, normalizer: Normalizer, search_id: int | None,
           stats: IngestStats, events: RunEvents) -> Listing:
    now = utcnow()
    listing = session.exec(
        select(Listing).where(Listing.source == raw.source, Listing.external_id == raw.external_id)
    ).first()
    is_new = listing is None
    if is_new:
        listing = Listing(source=raw.source, external_id=raw.external_id, url=raw.url)
    apply_raw(listing, raw, normalizer)
    listing.last_seen = now
    listing.missed_runs = 0
    if not listing.active:
        listing.active = True
    if raw.price and raw.price != listing.price:
        if listing.price:
            stats.price_changes += 1
            if raw.price < listing.price:
                events.price_drops.append((listing.id, listing.price, raw.price))
        listing.last_price = listing.price
        listing.price = raw.price
        session.add(listing)
        session.flush()
        session.add(PricePoint(listing_id=listing.id, price=raw.price, observed_at=now))
    session.add(listing)
    session.flush()

    if search_id is not None:
        hit = session.get(SearchHit, (search_id, listing.id))
        if hit is None:
            session.add(SearchHit(saved_search_id=search_id, listing_id=listing.id, last_seen=now))
        else:
            hit.last_seen = now
            session.add(hit)
    if is_new:
        stats.new += 1
    stats.found += 1
    return listing


def assign_vehicle(session: Session, listing: Listing, events: RunEvents) -> Vehicle:
    if listing.vehicle_id:
        return session.get(Vehicle, listing.vehicle_id)
    cfg = get_setting(session, "dedupe")
    cands = dedupe.candidates(session, listing)
    if cands and cands[0][1].score >= cfg["auto_threshold"]:
        v, ms = cands[0]
        listing.match_confidence = ms.score
    else:
        v = new_vehicle_from(listing)
        session.add(v)
        session.flush()
        events.new_vehicles.add(v.id)
    listing.vehicle_id = v.id
    session.add(listing)
    session.flush()
    return v


def mark_missing(session: Session, search_id: int, source: str, seen_ids: set[int],
                 stats: IngestStats, events: RunEvents) -> None:
    """Listings this search used to find on `source` but didn't now → missed_runs++."""
    threshold = int(get_setting(session, "inactive_after_runs"))
    rows = session.exec(
        select(Listing)
        .join(SearchHit, SearchHit.listing_id == Listing.id)
        .where(SearchHit.saved_search_id == search_id, Listing.source == source, Listing.active)
    ).all()
    for li in rows:
        if li.id in seen_ids:
            continue
        li.missed_runs += 1
        if li.missed_runs >= threshold:
            li.active = False
            stats.inactivated += 1
            if li.vehicle_id:
                events.inactivated.add(li.vehicle_id)
                stats.touched_vehicles.add(li.vehicle_id)
        session.add(li)


def refresh_vehicles(session: Session, ids: set[int]) -> None:
    for vid in ids:
        v = session.get(Vehicle, vid)
        if v:
            aggregate(session, v)
            session.add(v)
