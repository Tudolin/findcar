"""Idempotent seed: default search, aliases, red flags, model specs, sources.

Run with `python -m app.seed` (the `migrate` container does it after `alembic upgrade`).
"""

from sqlmodel import Session, select

from app.core.db import session_scope
from app.models import Alias, ModelSpec, RedFlagRule, SavedSearch, SourceStatus

RMC_CURITIBA = [
    "Adrianópolis", "Agudos do Sul", "Almirante Tamandaré", "Araucária", "Balsa Nova",
    "Bocaiúva do Sul", "Campina Grande do Sul", "Campo do Tenente", "Campo Largo", "Campo Magro",
    "Cerro Azul", "Colombo", "Contenda", "Curitiba", "Doutor Ulysses", "Fazenda Rio Grande",
    "Itaperuçu", "Lapa", "Mandirituba", "Piên", "Pinhais", "Piraquara", "Quatro Barras",
    "Quitandinha", "Rio Branco do Sul", "Rio Negro", "São José dos Pinhais", "Tijucas do Sul",
    "Tunas do Paraná",
]

DEFAULT_MODELS = [
    ("Honda", "Fit"), ("Honda", "City"), ("Hyundai", "HB20"), ("Hyundai", "HB20S"),
    ("Fiat", "Argo"), ("Ford", "Fiesta"), ("Ford", "Focus"),
]

BRAND_ALIASES = {
    "honda": "Honda", "hyundai": "Hyundai", "fiat": "Fiat", "ford": "Ford",
    "chevrolet": "Chevrolet", "gm": "Chevrolet", "gm chevrolet": "Chevrolet",
    "volkswagen": "Volkswagen", "vw": "Volkswagen", "vw volkswagen": "Volkswagen",
    "toyota": "Toyota", "renault": "Renault", "nissan": "Nissan", "peugeot": "Peugeot",
    "citroen": "Citroën",
}

MODEL_ALIASES = [
    ("Honda", "fit", "Fit"), ("Honda", "new fit", "Fit"), ("Honda", "city", "City"),
    ("Hyundai", "hb20", "HB20"), ("Hyundai", "hb 20", "HB20"),
    ("Hyundai", "hb20s", "HB20S"), ("Hyundai", "hb 20s", "HB20S"), ("Hyundai", "hb20 s", "HB20S"),
    ("Hyundai", "hb20x", "HB20X"),
    ("Fiat", "argo", "Argo"),
    ("Ford", "fiesta", "Fiesta"), ("Ford", "new fiesta", "Fiesta"),
    ("Ford", "fiesta sedan", "Fiesta"), ("Ford", "focus", "Focus"),
    ("Ford", "focus sedan", "Focus"), ("Ford", "focus hatch", "Focus"),
]

VERSION_ALIASES = [
    ("Hyundai", "c style", "comfort style"), ("Hyundai", "c plus", "comfort plus"),
    ("Hyundai", "comf style", "comfort style"), ("Hyundai", "comf plus", "comfort plus"),
    ("Hyundai", "comfort st", "comfort style"),
    (None, "automatico", "aut"), (None, "automatica", "aut"), (None, "mecanico", "mec"),
]

RED_FLAGS = [
    ("Leilão", r"leil[aã]o", 30),
    ("Sinistro", r"sinistr", 30),
    ("Recuperado de financiamento", r"recuperad[oa]s?\s+(de\s+)?financ", 25),
    ("Chassi remarcado", r"chassi\s+remarcad|remarcad[oa]", 30),
    ("Batido/enchente", r"\b(batid[oa]|colis[aã]o|enchente|alagad[oa])\b", 20),
    ("Repasse", r"\brepasse\b", 15),
    ("Vendido no estado", r"\bno estado\b", 15),
    ("Documentação atrasada", r"(doc|documento|ipva|licenciamento)\w*\s+(atrasad|vencid)", 10),
    ("Motor feito/retificado", r"motor\s+(feito|retificad|refeito)", 10),
    ("Alienado/financiado", r"alienad[oa]|quitar\s+financiamento", 5),
    ("Câmbio Powershift (histórico de falhas)", r"power\s*shift", 15),
    ("Câmbio AL4 (histórico de falhas)", r"\bal4\b", 15),
]

# Approximate INMETRO-style figures (gasoline, km/l). Editable in Configurações.
MODEL_SPECS = [
    ("Honda", "Fit", 11.0, 13.0), ("Honda", "City", 10.6, 12.6),
    ("Hyundai", "HB20", 12.3, 14.0), ("Hyundai", "HB20S", 11.5, 13.5),
    ("Fiat", "Argo", 12.6, 13.9), ("Ford", "Fiesta", 10.5, 12.5), ("Ford", "Focus", 9.0, 11.5),
]


def seed(session: Session) -> None:
    if not session.exec(select(SavedSearch)).first():
        session.add(SavedSearch(
            name="Curitiba e RMC até R$ 50 mil",
            filters={
                "models": [{"brand": b, "model": m} for b, m in DEFAULT_MODELS],
                "max_price": 50000, "min_year": 2009, "max_km": None,
                "automatic_only": False, "state": "pr", "city": "curitiba",
                "cities": RMC_CURITIBA, "max_pages": 3,
            },
            sources=["webmotors", "olx", "socarrao"],
        ))
    if not session.exec(select(Alias)).first():
        for pat, canon in BRAND_ALIASES.items():
            session.add(Alias(kind="brand", pattern=pat, canonical=canon))
        for brand, pat, canon in MODEL_ALIASES:
            session.add(Alias(kind="model", pattern=pat, canonical=canon, brand=brand))
        for brand, pat, canon in VERSION_ALIASES:
            session.add(Alias(kind="version", pattern=pat, canonical=canon, brand=brand))
    if not session.exec(select(RedFlagRule)).first():
        for label, pattern, penalty in RED_FLAGS:
            session.add(RedFlagRule(label=label, pattern=pattern, penalty=penalty))
    if not session.exec(select(ModelSpec)).first():
        for brand, model, city, road in MODEL_SPECS:
            session.add(ModelSpec(brand=brand, model=model, consumption_city=city,
                                  consumption_road=road, notes="aprox."))
    if session.get(SourceStatus, "webmotors") is None:
        session.add(SourceStatus(name="webmotors", enabled=True))
    if session.get(SourceStatus, "socarrao") is None:
        session.add(SourceStatus(name="socarrao", enabled=True))
    if session.get(SourceStatus, "olx") is None:
        # Read through headless Chromium (plain HTTP gets Cloudflare 403) — see docs/sources.md.
        session.add(SourceStatus(name="olx", enabled=True))


def main() -> None:
    with session_scope() as session:
        seed(session)
    print("seed ok")


if __name__ == "__main__":
    main()
