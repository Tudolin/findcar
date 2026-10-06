from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session, select

from app.api.templating import templates
from app.core.db import get_session
from app.models import SourceStatus
from app.services import runner, scheduler, stats, telegram

router = APIRouter()


def _sources(session: Session) -> list[SourceStatus]:
    return list(session.exec(select(SourceStatus).order_by(SourceStatus.name)).all())


@router.get("/healthz")
def healthz(session: Session = Depends(get_session)):
    session.exec(text("SELECT 1"))
    return {"ok": True}


@router.get("/health.json")
def health_json(session: Session = Depends(get_session)):
    srcs = _sources(session)
    return JSONResponse({
        "ok": True,
        "running": runner.is_running(),
        "next_runs": [t.isoformat() for t in scheduler.next_runs()],
        "telegram": telegram.configured(),
        "sources": [s.model_dump(mode="json") for s in srcs],
    })


@router.get("/health")
def health_page(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "health.html", {
        "nav": "health",
        "sources": _sources(session),
        "runs": stats.recent_runs(session, 25),
        "running": runner.is_running(),
        "next_runs": scheduler.next_runs(),
        "telegram": telegram.configured(),
    })
