"""Adapters against REAL pages captured 2026-10-06 (see tests/fixtures/README.md)."""

import httpx

from app.adapters import SearchFilters
from app.adapters.olx import OlxAdapter, parse_card_date
from app.adapters.webmotors import WebmotorsAdapter
from app.core.http import BlockedError
from app.seed import RMC_CURITIBA
from tests.conftest import FakeBrowser, fast_client, fixture_text

WM_SEARCH = fixture_text("webmotors/search_fit.html")
WM_DETAIL = fixture_text("webmotors/detail.html")
OLX_SEARCH = fixture_text("olx/search_fit.html")
OLX_DETAIL = fixture_text("olx/detail.html")


def _wm(handler, browser=None):
    return WebmotorsAdapter(fast_client(handler), browser=browser)


# -- Webmotors ---------------------------------------------------------------------
def test_webmotors_cards():
    items = _wm(lambda r: None).parse_search(WM_SEARCH)
    assert len(items) == 35
    first = items[0]
    assert first.external_id == "80053571"
    assert (first.brand, first.model, first.version) == ("HONDA", "FIT", "1.4 lx 16v flex 4p automático")
    assert (first.year_fab, first.year_model, first.km) == (2008, 2009, 102000)
    assert (first.city, first.state, first.price) == ("Curitiba", "pr", 44900)
    assert first.photos[0].startswith("https://image.webmotors.com.br/_fotos/") and "?" not in first.photos[0]
    assert items[1].city == "Pato Branco" and items[1].km == 235000
    assert all(i.km and i.city and i.price for i in items)


def test_webmotors_catalog_fallback():
    items = _wm(lambda r: None).parse_catalog(WM_SEARCH)
    assert len(items) == 24
    assert (items[0].year_fab, items[0].year_model, items[0].price) == (2008, 2009, 44900)


def test_webmotors_detail_jsonld():
    url = "https://www.webmotors.com.br/comprar/honda/fit/14-lx-16v-flex-4p-automatico/4-portas/2008-2009/80053571"
    d = _wm(lambda r: None).parse_detail(WM_DETAIL, url)
    assert d.external_id == "80053571"
    assert (d.color, d.transmission, d.km) == ("Dourado", "Automática", 102000)
    assert d.seller_type == "loja" and d.seller_name == "MCK VEICULOS"
    assert "IPVA pago" in d.description


def test_webmotors_http_then_local_filters():
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        return httpx.Response(200, text=WM_SEARCH)

    f = SearchFilters(brand="Honda", model="Fit", max_price=45000, min_year=2009, cities=RMC_CURITIBA, max_pages=3)
    items = _wm(handler).search(f)
    assert items and all(i.price <= 45000 and i.city in RMC_CURITIBA for i in items)
    assert "precoate=45000" in seen[0] and "anode=2009" in seen[0] and "/carros/pr/honda/fit?" in seen[0]
    assert len(seen) == 2  # page 2 repeated page 1 → stop


def test_webmotors_falls_back_to_browser_when_blocked():
    browser = FakeBrowser(WM_SEARCH)
    blocked = _wm(lambda r: httpx.Response(403, text="Access to this page has been denied"), browser)
    items = blocked.search(SearchFilters(brand="Honda", model="Fit", max_pages=1))
    assert len(items) == 35 and len(browser.urls) == 1


def test_webmotors_browser_captcha_still_blocks():
    class CaptchaBrowser(FakeBrowser):
        def get_html(self, url):
            raise BlockedError("captcha")

    a = _wm(lambda r: httpx.Response(403, text="px-captcha"), CaptchaBrowser(""))
    try:
        a.search(SearchFilters(brand="Honda", model="Fit", max_pages=1))
    except BlockedError:
        return
    raise AssertionError("expected BlockedError")


# -- OLX -----------------------------------------------------------------------------
def test_olx_cards():
    items = OlxAdapter(fast_client(lambda r: None)).parse_search(OLX_SEARCH, brand="Honda")
    assert len(items) == 50
    a = items[0]
    assert a.external_id == "1541869050"
    assert a.title == "Honda Fit LX 1.4/ 1.4 Flex 8v/16v 5P Mec. 2004"
    assert (a.price, a.km, a.color, a.year_model) == (24999, 235000, "Cinza", 2004)
    assert (a.city, a.neighborhood) == ("Curitiba", "Xaxim")
    assert a.photos[0] == "https://img.olx.com.br/images/76/768642216326557.webp"
    assert all(i.price and i.km for i in items)


def test_olx_detail_initial_data():
    d = OlxAdapter(fast_client(lambda r: None)).parse_detail(OLX_DETAIL, "u")
    assert d.external_id == "1540279843"
    assert (d.color, d.transmission, d.fuel, d.km) == ("Cinza", "Automático", "Flex", 266000)
    assert d.seller_type == "loja" and d.city == "Curitiba" and d.neighborhood == "Boqueirão"
    assert len(d.photos) == 13 and "Câmbio: Automático" in d.description and "<br>" not in d.description


def test_olx_search_uses_browser_and_filters():
    browser = FakeBrowser(OLX_SEARCH)
    f = SearchFilters(brand="Honda", model="Fit", max_price=50000, min_year=2009, cities=RMC_CURITIBA, max_pages=2)
    items = OlxAdapter(fast_client(lambda r: None), browser=browser).search(f)
    assert items and all((i.year_model or 0) >= 2009 and i.city in RMC_CURITIBA for i in items)
    assert "/honda/fit/estado-pr/regiao-de-curitiba-e-paranagua?o=1&pe=50000" in browser.urls[0]


def test_olx_url_fallback_when_region_empty():
    def pages(url):
        return OLX_SEARCH if "regiao-de" not in url else "<html><body>nada</body></html>"

    browser = FakeBrowser(pages)
    items = OlxAdapter(fast_client(lambda r: None), browser=browser).search(
        SearchFilters(brand="Honda", model="Fit", max_pages=1))
    assert items and "regiao-de" not in browser.urls[-1]


def test_model_guard_rejects_other_models():
    f = SearchFilters(brand="Hyundai", model="HB20", max_pages=1)
    items = OlxAdapter(fast_client(lambda r: None), browser=FakeBrowser(OLX_SEARCH)).search(f)
    assert items == []  # page full of Honda Fit → nothing passes an HB20 search


def test_card_dates():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime(2026, 10, 6, 20, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert parse_card_date("Hoje, 16:17", now) == datetime(2026, 10, 6, 19, 17)
    assert parse_card_date("Ontem, 08:00", now) == datetime(2026, 10, 5, 11, 0)
    assert parse_card_date("28 de dez, 10:00", now) == datetime(2025, 12, 28, 13, 0)
    assert parse_card_date("??", now) is None


def test_olx_title_helpers():
    from app.adapters.olx import _transmission_from_title, _year_from_title
    from app.services.normalize import normalize_transmission as nt

    assert _year_from_title("Hyundai HB20 Comfort Plus 1.0 2025 Leia a descrição") == 2025
    assert _year_from_title("Honda Fit LX 1.4/ 1.4 Flex 8v/16v 5P Mec. 2004") == 2004
    assert nt(_transmission_from_title("Hyundai HB20 Comf./c.plus/c.style 1.0 Flex 12V 2014")) == "manual"
    assert nt(_transmission_from_title("Honda Fit Ex/s/ex 1.5 Flex/flexone 16V 5P Aut. 2010")) == "automatico"
    assert _transmission_from_title("Fit 2010 lindo") is None
