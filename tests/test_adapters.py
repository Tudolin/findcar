import httpx

from app.adapters import SearchFilters
from app.adapters.olx import OlxAdapter
from app.adapters.webmotors import WebmotorsAdapter
from app.seed import RMC_CURITIBA
from tests.conftest import fast_client, fixture_text


def test_webmotors_parse_search():
    items = WebmotorsAdapter(fast_client(lambda r: None)).parse_search(
        fixture_text("webmotors/search_fit.html"))
    assert len(items) == 4
    fit = items[0]
    assert fit.external_id == "51234567"
    assert fit.price == 62900 and fit.km == 98000
    assert fit.year_fab == 2014 and fit.year_model == 2015
    assert fit.seller_type == "particular" and fit.state == "pr"
    assert fit.photos[0].startswith("https://image.webmotors.com.br/")
    assert "/comprar/honda/fit/" in fit.url
    assert items[1].seller_type == "loja"


def test_webmotors_search_applies_local_filters_and_paginates():
    html = fixture_text("webmotors/search_fit.html")
    pages = []

    def handler(req: httpx.Request):
        pages.append(str(req.url))
        return httpx.Response(200, text=html)

    f = SearchFilters(brand="Honda", model="Fit", max_price=50000, min_year=2009,
                      cities=RMC_CURITIBA, max_pages=3)
    items = WebmotorsAdapter(fast_client(handler)).search(f)
    # 62.900 > max; Ponta Grossa outside RMC; 2008 < min_year → only the 2012 LX remains
    assert [i.external_id for i in items] == ["51234568"]
    assert len(pages) == 2  # page 2 returned nothing new → stop
    assert "precoate=50000" in pages[0] and "anode=2009" in pages[0]


def test_webmotors_without_next_data_returns_empty():
    assert WebmotorsAdapter(fast_client(lambda r: None)).parse_search("<html></html>") == []


def test_olx_parse_search_and_detail():
    adapter = OlxAdapter(fast_client(lambda r: httpx.Response(
        200, text=fixture_text("olx/detail.html"))))
    items = adapter.parse_search(fixture_text("olx/search_fit.html"))
    assert len(items) == 2
    a = items[0]
    assert a.price == 61500 and a.km == 98500 and a.year_model == 2015
    assert a.city == "Curitiba" and a.neighborhood == "Água Verde"
    assert a.seller_type == "particular" and items[1].seller_type == "loja"
    assert adapter.fetch_detail(a.url).description == "Único dono, nunca batido."
