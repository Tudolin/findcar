import json

from app.core.http import PoliteClient
from app.models import FipeCache, Vehicle
from app.services.fipe import FipeService
from tests.conftest import fixture_text


def _svc(session):
    return FipeService(session, client=PoliteClient(min_delay=0, max_delay=0, cache_ttl=0))


def test_rank_models_prefers_version_and_transmission():
    models = json.loads(fixture_text("fipe/honda_models.json"))
    aut = FipeService.rank_models(models, "Fit", "1.5 EX 16V Flex Aut", "automatico")
    assert aut[0]["code"] == "10815"  # Fit EX/S 1.5 Flex/Flexone 16V 5p Aut.
    mec = FipeService.rank_models(models, "Fit", "1.4 LX 16V", "manual")
    assert "Mec" in mec[0]["name"] and "LX 1.4" in mec[0]["name"]
    assert all(m["name"].startswith("Fit") for m in aut)


def test_match_and_cache(session, fipe_mock):
    svc = _svc(session)
    m = svc.match("Honda", "Fit", "1.5 EX 16V Flex Aut", "automatico", 2015)
    assert m and m.price == 29684 and m.code == "014045-7"
    calls = fipe_mock.calls.call_count
    svc.match("Honda", "Fit", "1.5 EX 16V Flex Aut", "automatico", 2015)
    assert fipe_mock.calls.call_count == calls  # served from fipe_cache
    assert session.get(FipeCache, "/cars/brands") is not None


def test_update_vehicle_sets_diff(session):
    v = Vehicle(brand="Honda", model="Fit", version="1.5 EX", transmission="automatico",
                year_model=2015, price=32653)
    assert _svc(session).update_vehicle(v)
    assert v.fipe_price == 29684 and v.fipe_diff_pct == 10.0


def test_unknown_model_is_graceful(session):
    v = Vehicle(brand="Marca Inexistente", model="X", year_model=2015, price=1)
    assert _svc(session).update_vehicle(v) is False
