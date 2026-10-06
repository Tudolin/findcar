from app.services.normalize import Normalizer, norm, normalize_seller, normalize_transmission


def test_norm():
    assert norm("HB20 C.Style 1.0") == "hb20 c style 1.0"
    assert norm("São José dos Pinhais") == "sao jose dos pinhais"


def test_transmission_and_seller():
    assert normalize_transmission("Automática") == "automatico"
    assert normalize_transmission("1.5 EX FLEX AUT.") == "automatico"
    assert normalize_transmission("CVT") == "automatico"
    assert normalize_transmission("Manual") == "manual"
    assert normalize_transmission("1.0 MEC") == "manual"
    assert normalize_transmission("") is None
    assert normalize_seller("PJ") == "loja" and normalize_seller("PF") == "particular"


def test_aliases_from_seed(session):
    n = Normalizer.from_db(session)
    a = n.resolve("HYUNDAI", "HYUNDAI HB20S 1.6 C.STYLE 16V FLEX 4P AUT", None)
    b = n.resolve("Hyundai", "HB20S", "1.6 Comfort Style 16V Flex 4P Aut")
    assert (a.brand, a.model) == ("Hyundai", "HB20S")
    assert a.version == b.version
    c = n.resolve("HONDA", "FIT", "1.5 EX 16V FLEX 4P AUTOMÁTICO")
    assert (c.brand, c.model, c.version) == ("Honda", "Fit", "1.5 EX 16V FLEX 4P Aut")
    assert n.resolve("HYUNDAI", "HB 20", "1.0").model == "HB20"
