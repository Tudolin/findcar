"""DEV ONLY: fills a local database with fake listings to preview the UI.

    DATABASE_URL=... python scripts/demo_data.py

Never run it against your real database: everything here is invented.
"""

import random
from dataclasses import replace
from datetime import timedelta

from sqlmodel import select

from app.adapters.base import RawListing, SourceAdapter
from app.core.db import session_scope
from app.core.timeutil import utcnow
from app.models import Listing, PricePoint, SavedSearch, Vehicle
from app.services.config_store import set_setting
from app.services.runner import run_search_source

random.seed(7)
MODELS = [("HONDA", "FIT", ["1.5 EX 16V FLEX 4P AUTOMÁTICO", "1.4 LX 16V FLEX 4P MANUAL",
                           "1.5 EXL 16V FLEX 4P AUTOMÁTICO"], 11),
          ("HONDA", "CITY", ["1.5 EX 16V FLEX 4P AUTOMÁTICO", "1.5 LX 16V FLEX 4P MANUAL"], 6),
          ("HYUNDAI", "HB20", ["1.0 COMFORT PLUS 12V FLEX 4P MANUAL", "1.6 COMFORT STYLE 16V FLEX 4P AUTOMÁTICO"], 12),
          ("HYUNDAI", "HB20S", ["1.6 C.STYLE 16V FLEX 4P AUTOMÁTICO"], 5),
          ("FIAT", "ARGO", ["1.0 FIREFLY FLEX DRIVE MANUAL"], 6),
          ("FORD", "FIESTA", ["1.6 TITANIUM HATCH 16V FLEX 4P POWERSHIFT", "1.6 SE HATCH 16V FLEX 4P MANUAL"], 7),
          ("FORD", "FOCUS", ["2.0 SE 16V FLEX 4P POWERSHIFT"], 4)]
CITIES = ["Curitiba", "Curitiba", "Curitiba", "São José dos Pinhais", "Colombo", "Pinhais", "Araucária"]
COLORS = ["Prata", "Preto", "Branco", "Cinza", "Vermelho"]
DESCS = ["Único dono, revisões na concessionária.", "Carro de repasse, vendido no estado.",
         "Pneus novos, IPVA pago.", "Aceito troca. Carro de leilão com laudo aprovado.", ""]
PHOTO = "https://picsum.photos/seed/{}/640/400"


def build():
    out = []
    n = 0
    for brand, model, versions, count in MODELS:
        for _ in range(count):
            n += 1
            year = random.randint(2010, 2019)
            version = random.choice(versions)
            out.append(RawListing(
                source="webmotors", external_id=f"D{n}", url=f"https://www.webmotors.com.br/demo/{n}",
                title=f"{brand} {model} {version}", brand=brand, model=model,
                version=version, year_fab=year - random.randint(0, 1), year_model=year,
                km=random.randint(25, 170) * 1000, transmission=None, color=random.choice(COLORS),
                price=random.randint(29, 50) * 1000 - random.choice([0, 100, 500, 900]),
                city=random.choice(CITIES), state="pr", seller_type=random.choice(["PF", "PJ"]),
                photos=[PHOTO.format(n), PHOTO.format(n + 1000)], description=random.choice(DESCS),
                has_detail=True))
    return out


class Demo(SourceAdapter):
    name, supports_detail = "webmotors", False

    def __init__(self, items):
        self.items = items

    def search(self, f):
        return [i for i in self.items if i.model.lower() == f.model.lower()]


def main():
    items = build()
    with session_scope() as s:
        set_setting(s, "dedupe", {"photo_hash": False})
        search = s.exec(select(SavedSearch)).first()
        run_search_source(s, search, "webmotors", Demo(items))
        dup = [replace(i, source="olx", external_id="O" + i.external_id, url="https://www.olx.com.br/demo",
                       price=i.price - 700, km=i.km + 300) for i in items[:5]]
        run_search_source(s, search, "olx", Demo(dup))
        # backdate history so charts have something to show
        for li in s.exec(select(Listing)).all():
            pts = s.exec(select(PricePoint).where(PricePoint.listing_id == li.id)).all()
            for p in pts:
                p.observed_at = utcnow() - timedelta(days=random.randint(40, 100))
                s.add(p)
            for k in range(random.randint(0, 3)):
                s.add(PricePoint(listing_id=li.id, price=li.price + (3 - k) * 800,
                                 observed_at=utcnow() - timedelta(days=30 - k * 9)))
            li.first_seen = utcnow() - timedelta(days=100)
            s.add(li)
        vs = s.exec(select(Vehicle).order_by(Vehicle.score.desc())).all()
        stages = ["interessante", "interessante", "contatado", "visitado", "novo", "descartado"]
        for v, stage in zip(vs[:6], stages, strict=False):
            v.stage = stage
            v.favorite = stage in {"contatado", "visitado"}
            s.add(v)
    print("demo ok")


if __name__ == "__main__":
    main()
