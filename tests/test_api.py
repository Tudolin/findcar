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
    r = client.get("/vehicles?transmission=automatico&sort=price", headers={"HX-Request": "true"})
    assert "1</b> veículo" in r.text
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


def test_pwa_assets(client):
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["display"] == "standalone"
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["service-worker-allowed"] == "/"
    assert client.get("/offline").status_code == 200
    for icon in m.json()["icons"]:
        assert client.get(icon["src"]).status_code == 200
    page = client.get("/")
    assert 'viewport-fit=cover' in page.text and 'rel="manifest"' in page.text and 'class="tabbar"' in page.text


# -- filters (regression: htmx submits every field, empty ones included) -------------
EMPTY_FORM = ("q=&model=&max_price=&max_km=&source=&transmission=&sort=score&status=active"
              "&min_price=&min_year=&max_year=&min_score=&seller=&city=&new=&stage=")


def test_filters_accept_empty_fields(client, data):
    r = client.get("/vehicles?" + EMPTY_FORM, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "2</b> veículos" in r.text


def test_filters_narrow_results(client, data):
    def count(qs):
        r = client.get(f"/vehicles?{EMPTY_FORM}&{qs}", headers={"HX-Request": "true"})
        assert r.status_code == 200, r.text[:300]
        import re
        return int(re.search(r'num" style="color:var\(--text\)">(\d+)</b>', r.text).group(1))

    assert count("transmission=automatico") == 1
    assert count("max_price=30.000") == 1  # thousands separator typed by the user
    assert count("max_km=100000") == 1
    assert count("min_year=2012") == 1
    assert count("no_flags=1") == 1  # the 2010 one has "repasse"/"no estado"
    assert count("dropped=1") == 1  # FIT went 34000 → 32000
    assert count("q=prata") == 1
    assert count("source=olx") == 0
    assert count("max_price=abc") == 2  # garbage is ignored, never a 422


def test_filter_chips_and_clear(client, data):
    r = client.get("/vehicles?transmission=automatico&below_fipe=1")
    assert r.status_code == 200
    assert 'class="chip" href="/vehicles?below_fipe=1"' in r.text  # removing "Automático"
    assert "Limpar tudo" in r.text


def test_load_more_pagination(client, session, data):
    from app.services import vehicle_filters

    vehicle_filters.PAGE_SIZE, old = 1, vehicle_filters.PAGE_SIZE
    import app.api.pages as pages
    pages.PAGE_SIZE = 1
    try:
        first = client.get("/vehicles")
        assert 'id="more"' in first.text and "offset=1" in first.text
        more = client.get("/vehicles?offset=1", headers={"HX-Request": "true"})
        assert more.status_code == 200 and 'class="card vcard' in more.text and 'id="more"' not in more.text
    finally:
        vehicle_filters.PAGE_SIZE = pages.PAGE_SIZE = old


# -- favorites ------------------------------------------------------------------------
def test_favorites_page_tracks_price_since_favorited(client, session, data):
    vid = data[0]
    client.post(f"/vehicles/{vid}/favorite")
    session.expire_all()
    v = session.get(Vehicle, vid)
    assert v.favorite and v.favorited_at and v.favorite_price == v.price
    v.favorite_price = v.price + 1500  # pretend it was more expensive when favorited
    session.add(v)
    session.commit()
    r = client.get("/favorites")
    assert r.status_code == 200 and "desde que favoritou" in r.text and "R$ 1.500" in r.text
    assert 'href="/favorites"' in client.get("/").text  # in the menu
    client.post(f"/vehicles/{vid}/favorite")
    session.expire_all()
    assert session.get(Vehicle, vid).favorited_at is None


def test_favorites_empty(client, engine):
    assert "Nenhum favorito ainda" in client.get("/favorites").text


# -- vehicle page extras --------------------------------------------------------------
def test_checklist_toggle(client, session, data):
    vid = data[0]
    r = client.post(f"/vehicles/{vid}/checklist/laudo")
    assert r.status_code == 200 and "1/10" in r.text
    session.expire_all()
    assert session.get(Vehicle, vid).checklist == {"laudo": True}
    assert client.post(f"/vehicles/{vid}/checklist/nao-existe").status_code == 400


def test_vehicle_page_market_and_whatsapp(client, session, data):
    vid = data[0]
    li = session.exec(select(Listing).where(Listing.vehicle_id == vid)).first()
    li.seller_phone = "41987803200"
    session.add(li)
    session.commit()
    r = client.get(f"/vehicles/{vid}")
    assert "Análise de mercado" in r.text and "Sugestão de proposta" in r.text
    assert "https://wa.me/5541987803200?text=" in r.text
    assert "Checklist da visita" in r.text


def test_finance_settings_and_compare_row(client, session, data):
    r = client.post("/settings/finance", data={"rate_month": "1,5", "down_pct": "40", "months": "36", "fees": "800"},
                    follow_redirects=False)
    assert r.status_code == 303
    from app.services.config_store import get_setting

    assert get_setting(session, "finance") == {"rate_month": 1.5, "down_pct": 40, "months": 36, "iof": False,
                                               "fees": 800}
    page = client.get(f"/vehicles/{data[0]}")
    assert "Simular financiamento" in page.text and '"rate_month": 1.5' in page.text.replace("&#34;", '"')
    cmp = client.get(f"/compare?ids={data[0]},{data[1]}")
    assert "Parcela (padrão)" in cmp.text
