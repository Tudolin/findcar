from dataclasses import replace

from sqlmodel import select

from app.adapters.base import RawListing, SourceAdapter
from app.core.http import BlockedError
from app.models import AlertLog, Listing, PricePoint, SavedSearch, SourceStatus, Vehicle
from app.services.config_store import set_setting
from app.services.runner import run_search_source

FIT = RawListing(source="webmotors", external_id="A1", url="https://wm/a1",
                 title="HONDA FIT 1.5 EX AUT", brand="HONDA", model="FIT",
                 version="1.5 EX 16V FLEX 4P AUTOMÁTICO", year_fab=2014, year_model=2015,
                 km=98000, transmission="Automática", color="Prata", price=34000,
                 city="Curitiba", state="pr", seller_type="PF", description="Único dono",
                 has_detail=True)


class FakeAdapter(SourceAdapter):
    supports_detail = False

    def close(self):
        pass

    def __init__(self, name, batches):
        self.name, self.batches, self.calls = name, list(batches), 0

    def search(self, filters):
        if filters.model != "Fit":  # the seeded search has 7 models; only Fit has data
            return []
        batch = self.batches[min(self.calls, len(self.batches) - 1)]
        self.calls += 1
        if isinstance(batch, Exception):
            raise batch
        return batch


def _search(session):
    s = session.exec(select(SavedSearch)).first()
    set_setting(session, "dedupe", {"photo_hash": False})
    return s


def test_price_history_and_sold_detection(session):
    s = _search(session)
    cheaper = replace(FIT, price=32000)
    ad = FakeAdapter("webmotors", [[FIT], [cheaper], [], [], []])
    run_search_source(session, s, "webmotors", ad)
    li = session.exec(select(Listing)).one()
    v = session.get(Vehicle, li.vehicle_id)
    assert v.price == 34000 and v.fipe_price == 29684 and v.score is not None
    assert v.brand == "Honda" and v.model == "Fit" and v.transmission == "automatico"

    run_search_source(session, s, "webmotors", ad)
    prices = [p.price for p in session.exec(select(PricePoint).order_by(PricePoint.id))]
    assert prices == [34000, 32000]
    assert li.last_price == 34000 and li.price == 32000

    for _ in range(3):  # default: inactive after 3 runs without the listing
        run_search_source(session, s, "webmotors", ad)
    session.refresh(li)
    session.refresh(v)
    assert li.active is False and v.active is False and v.price == 32000


def test_reappearing_listing_is_reactivated(session):
    s = _search(session)
    ad = FakeAdapter("webmotors", [[FIT], [], [], [], [FIT]])
    for _ in range(5):
        run_search_source(session, s, "webmotors", ad)
    li = session.exec(select(Listing)).one()
    assert li.active and li.missed_runs == 0


def test_cross_source_listings_merge_into_one_vehicle(session):
    s = _search(session)
    run_search_source(session, s, "webmotors", FakeAdapter("webmotors", [[FIT]]))
    olx = replace(FIT, source="olx", external_id="O9", url="https://olx/o9", km=98400,
                  price=33500, version="EX 1.5 FLEX 16V 5P AUT.", seller_type="particular")
    run_search_source(session, s, "olx", FakeAdapter("olx", [[olx]]))
    vehicles = session.exec(select(Vehicle)).all()
    assert len(vehicles) == 1
    assert vehicles[0].price == 33500
    li = session.exec(select(Listing).where(Listing.source == "olx")).one()
    assert li.match_confidence >= 0.75


def test_blocked_run_stops_and_does_not_inactivate(session):
    s = _search(session)
    ad = FakeAdapter("webmotors", [[FIT]] + [BlockedError("403 captcha")] * 5)
    for _ in range(5):
        run = run_search_source(session, s, "webmotors", ad)
    assert run.status == "blocked"
    assert session.exec(select(Listing)).one().active
    st = session.get(SourceStatus, "webmotors")
    assert st.last_status == "blocked" and st.blocked_since is not None
    assert len(session.exec(select(AlertLog).where(AlertLog.kind == "blocked")).all()) == 1


def test_price_drop_alert_logged_once(session):
    s = _search(session)
    set_setting(session, "alerts", {"drop_pct": 3})
    ad = FakeAdapter("webmotors", [[FIT], [replace(FIT, price=30000)], [replace(FIT, price=30000)]])
    for _ in range(3):
        run_search_source(session, s, "webmotors", ad)
    drops = session.exec(select(AlertLog).where(AlertLog.kind == "drop")).all()
    assert len(drops) == 1 and "34.000" in drops[0].message


def test_block_mid_run_keeps_and_processes_collected(session):
    s = _search(session)

    class HalfBlocked(FakeAdapter):
        def search(self, filters):
            if filters.model == "Fit":
                return [FIT]
            raise BlockedError("403 no segundo modelo")

    run = run_search_source(session, s, "webmotors", HalfBlocked("webmotors", [[FIT]]))
    assert run.status == "blocked" and run.n_found == 1
    li = session.exec(select(Listing)).one()
    v = session.get(Vehicle, li.vehicle_id)  # vehicle, FIPE and score despite the block
    assert v is not None and v.fipe_price == 29684 and v.score is not None
