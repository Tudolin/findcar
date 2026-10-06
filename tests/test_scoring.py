from sqlmodel import select

from app.models import ModelSpec, RedFlagRule, Vehicle
from app.services import scoring
from app.services.config_store import DEFAULTS


def _score(session, v, text=""):
    rules = list(session.exec(select(RedFlagRule)).all())
    spec = session.exec(select(ModelSpec).where(ModelSpec.model == v.model)).first()
    return scoring.compute(v, text, rules, DEFAULTS["score.weights"],
                           DEFAULTS["score.seller_values"], DEFAULTS["score.inferred_flags"], spec)


def test_good_car_scores_high(session):
    v = Vehicle(brand="Honda", model="Fit", year_model=2016, km=60000, transmission="automatico",
                seller_type="particular", fipe_diff_pct=-12.0)
    res = _score(session, v)
    assert res.score >= 75
    keys = {b["key"] for b in res.breakdown}
    assert {"price_fipe", "km_year", "age", "transmission", "seller", "consumption"} <= keys
    assert all(b["detail"] for b in res.breakdown)


def test_red_flags_penalize_and_explain(session):
    v = Vehicle(brand="Honda", model="Fit", year_model=2016, km=60000, transmission="automatico",
                seller_type="particular", fipe_diff_pct=-12.0)
    clean = _score(session, v).score
    flagged = _score(session, v, "Carro de LEILÃO, vendido no estado")
    assert {f["label"] for f in flagged.flags} == {"Leilão", "Vendido no estado"}
    assert flagged.score == clean - 45


def test_powershift_inferred_once(session):
    v = Vehicle(brand="Ford", model="Focus", year_model=2014, km=90000, transmission="automatico")
    res = _score(session, v, "Focus Powershift revisado")
    labels = [f["label"] for f in res.flags]
    assert labels.count("Câmbio Powershift (histórico de falhas)") == 1


def test_missing_data_is_ignored_not_zeroed(session):
    v = Vehicle(brand="Honda", model="Fit", year_model=2016, km=60000, transmission="automatico",
                seller_type="particular")  # no FIPE
    res = _score(session, v)
    fipe = next(b for b in res.breakdown if b["key"] == "price_fipe")
    assert fipe["points"] is None and res.score > 50


def test_too_cheap_is_flagged(session):
    v = Vehicle(brand="Hyundai", model="HB20", year_model=2025, km=36000, transmission="manual",
                fipe_diff_pct=-50.6)
    res = _score(session, v)
    assert any("abaixo da FIPE" in f["label"] for f in res.flags)
