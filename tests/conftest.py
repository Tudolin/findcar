import json
import os
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""

from app.core import db  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.http import PoliteClient  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def fixture_text(rel: str) -> str:
    path = FIX / rel
    if not path.exists() and (FIX / f"{rel}.gz").exists():
        import gzip

        return gzip.open(FIX / f"{rel}.gz", "rt", encoding="utf-8").read()
    return path.read_text(encoding="utf-8")


class FakeBrowser:
    """Stands in for BrowserFetcher: serves saved HTML, records URLs."""

    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get_html(self, url: str) -> str:
        self.urls.append(url)
        return self.pages(url) if callable(self.pages) else self.pages

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    import app.models  # noqa: F401

    SQLModel.metadata.create_all(eng)
    db.set_engine(eng)
    from app.seed import seed

    with Session(eng) as s:
        seed(s)
        s.commit()
    yield eng
    db.set_engine(None)


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def fipe_mock():
    """Real FIPE payloads for Honda Fit; everything else on the internet → 404."""
    base = "https://fipe.parallelum.com.br/api/v2/cars"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{base}/brands").respond(json=json.loads(fixture_text("fipe/brands.json")))
        router.get(f"{base}/brands/25/models").respond(
            json=json.loads(fixture_text("fipe/honda_models.json")))
        years = json.loads(fixture_text("fipe/fit_10815_years.json"))
        price = json.loads(fixture_text("fipe/fit_10815_price.json"))
        router.get(f"{base}/brands/25/models/10815/years").respond(json=years)
        router.get(url__regex=rf"{base}/brands/25/models/10815/years/\d{{4}}-1").mock(
            side_effect=lambda req: httpx.Response(200, json={
                **price, "modelYear": int(req.url.path.rsplit("/", 1)[1][:4])}))
        router.route().respond(404)
        yield router


def fast_client(handler) -> PoliteClient:
    return PoliteClient(min_delay=0, max_delay=0, cache_ttl=0,
                        transport=httpx.MockTransport(handler))
