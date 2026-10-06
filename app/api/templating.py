from datetime import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.timeutil import to_local, utcnow
from app.models import STAGE_LABELS, Stage

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def brl(v) -> str:
    return "—" if v is None else f"R$ {v:,.0f}".replace(",", ".")


def km(v) -> str:
    return "—" if v is None else f"{v:,.0f} km".replace(",", ".")


def num(v, digits: int = 0) -> str:
    return "—" if v is None else f"{v:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v) -> str:
    return "—" if v is None else f"{v:+.1f}%".replace(".", ",")


def dt(v: datetime | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    loc = to_local(v)
    return loc.strftime(fmt) if loc else "—"


def ago(v: datetime | None) -> str:
    if v is None:
        return "nunca"
    secs = int((utcnow() - v).total_seconds())
    if secs < 60:
        return "agora"
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if secs >= size:
            return f"há {secs // size} {unit}"
    return "agora"


def score_class(score) -> str:
    if score is None:
        return "score-none"
    return "score-good" if score >= 75 else "score-mid" if score >= 50 else "score-bad"


def fipe_class(p) -> str:
    if p is None:
        return ""
    return "pos" if p <= -3 else "neg" if p >= 5 else "neutral"


templates.env.filters.update(brl=brl, km=km, pct=pct, dt=dt, ago=ago, num=num,
                             score_class=score_class, fipe_class=fipe_class)
templates.env.globals.update(STAGES=list(Stage), STAGE_LABELS=STAGE_LABELS,
                             SOURCE_LABELS={"olx": "OLX", "webmotors": "Webmotors"},
                             TRANSMISSION={"automatico": "Automático", "manual": "Manual"})
