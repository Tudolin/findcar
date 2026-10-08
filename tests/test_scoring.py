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


def test_market_offer_and_comparables(session):
    from datetime import timedelta

    from app.core.timeutil import utcnow
    from app.services import market

    base = dict(brand="Honda", model="Fit", transmission="automatico", active=True)
    target = Vehicle(**base, year_model=2014, km=90000, price=50000, fipe_price=52000,
                     listed_since=utcnow() - timedelta(days=70), price_drop=2000)
    session.add(target)
    for i, p in enumerate([46000, 48000, 52000, 55000]):
        session.add(Vehicle(**base, year_model=2014 + (i % 2), km=85000 + i * 5000, price=p))
    session.add(Vehicle(**base, year_model=2019, km=30000, price=70000))  # too new: not comparable
    session.commit()
    comps = market.comparables(session, target)
    assert comps.count == 4 and comps.median_price == 50000 and comps.cheaper_than_pct == 50
    o = market.offer(target, comps)
    assert o.low < o.high <= 50000
    assert any("70 dias" in r for r in o.reasons) and any("baixou" in r for r in o.reasons)


def test_finance_price_table():
    from app.services import finance

    # Classic textbook check: R$ 10.000, 1% a.m., 12x → R$ 888,49
    assert round(finance.pmt(10000, 1.0, 12), 2) == 888.49
    assert finance.pmt(1200, 0, 12) == 100
    s = finance.simulate(price=50000, down=15000, months=48, rate_month=1.99, with_iof=False)
    assert s.financed == 35000 and s.installment == round(finance.pmt(35000, 1.99, 48))
    assert s.total_paid == 15000 + s.installment * 48 and s.interest == s.total_paid - 50000
    assert s.rate_year == 26.7
    with_iof = finance.simulate(50000, 15000, 48, 1.99, with_iof=True)
    assert with_iof.iof == round(35000 * finance.iof_rate(48)) == 1181 and with_iof.installment > s.installment
    assert finance.simulate(50000, 80000, 48, 2).installment == 0  # down ≥ price: nothing financed
    assert [t.months for t in finance.table(40000, finance.DEFAULTS)] == [12, 24, 36, 48, 60]
