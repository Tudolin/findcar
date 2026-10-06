from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import actions, health, pages
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services import scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging(get_settings().log_level)
    scheduler.start()
    yield
    scheduler.shutdown()


def create_app(with_scheduler: bool = True) -> FastAPI:
    app = FastAPI(title="carwatch", lifespan=lifespan if with_scheduler else None,
                  docs_url="/api/docs", redoc_url=None)
    static = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")
    app.include_router(health.router)
    app.include_router(pages.router)
    app.include_router(actions.router)
    return app


app = create_app()
