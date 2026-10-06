from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.main import create_app
from app.models import KanbanEvent, Listing, SavedSearch, Vehicle
from app.services.config_store import set_setting
from app.services.runner import run_search_source
from tests.test_ingest import FIT, FakeAdapter


@pytest.fixture
def client(engine):
    return TestClient(create_app(with_scheduler=False))


@pytest.fixture
def data(session):
    s = session.exec(select(SavedSearch)).first()
    set_setting(session, "dedupe", {"photo_hash": False})
    other = replace(FIT, external_id="B2", url="https://wm/b2", km=150000, price=28000,
                    color="Preto", transmission="Manual", version="1.4 LX 16V FLEX MANUAL",
                    year_fab=2010, year_model=2010, description="Repasse, no estado")
    run_search_source(session, s, "webmotors", FakeAdapter("webmotors", [[FIT, other]]))
    run_search_source(session, s, "webmotors", FakeAdapter("webmotors", [[replace(FIT, price=32000), other]]))
    session.commit()
    return [v.id for v in session.exec(select(Vehicle).order_by(Vehicle.id)).all()]


@pytest.mark.parametrize("path", ["/", "/vehicles", "/kanban", "/searches", "/searches/new",
                                  "/settings", "/health", "/health.json", "/healthz", "/compare",
                                  "/searches/1/edit", "/api/median?model=Fit"])
def test_pages_render(client, data, path):
    r = client.get(path)
    assert r.status_code == 200, r.text[:500]


def test_vehicle_detail_and_filters(client, data):
    r = client.get(f"/vehicles/{data[0]}")
    assert r.status_code == 200 and "Histórico de preço" in r.text and "Score" in r.text
    r = client.get("/vehicles?automatic=true&sort=price", headers={"HX-Request": "true"})
    assert "1 veículo" in r.text
    assert client.get("/vehicles/9999").status_code == 404


def test_compare_and_csv(client, data):
    ids = ",".join(map(str, data))
    r = client.get(f"/compare?ids={ids}")
    assert r.status_code == 200 and 'class="num best"' in r.text
    csv = client.get(f"/compare.csv?ids={ids}")
    assert csv.headers["content-type"].startswith("text/csv")
    assert "Preço;" in csv.text and "32000" in csv.text


def test_stage_notes_favorite(client, data, session):
    vid = data[0]
    assert client.post(f"/vehicles/{vid}/stage", data={"stage": "contatado"}).status_code == 204
    assert client.post(f"/vehicles/{vid}/notes", data={"notes": "ligar sábado"}).status_code == 204
    assert "fav on" in client.post(f"/vehicles/{vid}/favorite").text
    session.expire_all()
    v = session.get(Vehicle, vid)
    assert (v.stage, v.notes, v.favorite) == ("contatado", "ligar sábado", True)
    assert session.exec(select(KanbanEvent)).one().to_stage == "contatado"
    assert client.post(f"/vehicles/{vid}/stage", data={"stage": "xx"}).status_code == 400


def test_merge_and_split(client, data, session):
    a, b = data
    r = client.post(f"/vehicles/{a}/merge", data={"other_id": b}, follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert session.get(Vehicle, b) is None
    li = session.exec(select(Listing).where(Listing.external_id == "B2")).one()
    assert li.vehicle_id == a and li.match_manual
    client.post(f"/listings/{li.id}/split")
    session.expire_all()
    assert session.get(Listing, li.id).vehicle_id != a


def test_search_crud(client, engine, session):
    r = client.post("/searches", data={"name": "Automáticos", "models": "Honda City\nFiat Argo",
                                       "max_price": "45.000", "min_year": "2014",
                                       "automatic_only": "on", "sources": "webmotors",
                                       "enabled": "on"}, follow_redirects=False)
    assert r.status_code == 303
    s = session.exec(select(SavedSearch).where(SavedSearch.name == "Automáticos")).one()
    assert s.filters["models"][1] == {"brand": "Fiat", "model": "Argo"}
    assert s.filters["max_price"] == 45000 and s.filters["automatic_only"]
    sid = s.id
    client.post(f"/searches/{sid}/delete")
    session.expire_all()
    assert session.get(SavedSearch, sid) is None


def test_settings_forms(client, data):
    assert client.post("/settings/score", data={"w_price_fipe": "50"}).status_code == 200
    assert client.post("/settings/flags", data={"label": "Teste", "pattern": "único dono",
                                                "penalty": "3"}).status_code == 200
    assert client.post("/settings/flags", data={"label": "x", "pattern": "("}).status_code == 200
    assert client.post("/settings/schedule", data={"times": "07:05, 18:30"}).status_code == 200
    assert client.post("/settings/alerts", data={"min_score": "60"}).status_code == 200
    assert client.post("/sources/olx/toggle").status_code == 200
