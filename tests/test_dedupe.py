from app.models import Listing
from app.services.dedupe import compare, dhash, hamming


def _li(**kw):
    base = dict(source="webmotors", external_id="1", url="u", brand="Honda", model="Fit",
                version="1.5 EX 16V Flex Aut", year_model=2015, km=98000, color="Prata",
                city="Curitiba", price=62900)
    base.update(kw)
    return Listing(**base)


def test_same_car_across_sources_scores_high():
    a = _li()
    b = _li(source="olx", external_id="2", km=98500, price=61500, version="EX 1.5 Flex 16V Aut")
    ms = compare(a, b)
    assert ms.score >= 0.75 and "cor" in ms.reasons


def test_different_color_or_year_is_rejected():
    assert compare(_li(), _li(year_model=2014)) is None
    assert compare(_li(), _li(color="Preto", km=60000)).score < 0.5


def test_dhash_detects_same_image():
    import io

    from PIL import Image

    img = Image.new("RGB", (64, 48))
    for x in range(64):
        for y in range(48):
            img.putpixel((x, y), (x * 4, y * 5, 128))
    buf1, buf2 = io.BytesIO(), io.BytesIO()
    img.save(buf1, "PNG")
    img.resize((128, 96)).save(buf2, "JPEG", quality=70)
    assert hamming(dhash(buf1.getvalue()), dhash(buf2.getvalue())) <= 6


def test_different_dealers_and_prices_do_not_merge():
    # Real case: same model/year/km≈, but another dealer asking 20% more → different car.
    a = _li(source="olx", external_id="o", km=204000, price=39800, color="Preto", seller_name="Exclusiva Veículos")
    b = _li(source="webmotors", external_id="w", km=204500, price=47900, color="Preto", seller_name="GRIFFE AUTO")
    assert compare(a, b).score < 0.75


def test_same_dealer_across_sources_merges():
    a = _li(source="socarrao", external_id="s", km=204400, price=42880, color="Prata",
            seller_name="Autoban Multimarca")
    b = _li(source="olx", external_id="o", km=204400, price=42880, color="Prata", seller_name="AUTOBAN")
    ms = compare(a, b)
    assert ms.score >= 0.75 and "mesmo vendedor" in ms.reasons
