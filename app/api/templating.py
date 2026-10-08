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


def days_since(v: datetime | None) -> int | None:
    return None if v is None else max(0, (utcnow() - v).days)


def is_new(v: datetime | None, hours: int = 48) -> bool:
    return v is not None and (utcnow() - v).total_seconds() < hours * 3600


def wa_phone(phone: str | None) -> str | None:
    """Brazilian number → wa.me format (55 + DDD + number)."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) in (10, 11):
        return "55" + digits
    return digits if len(digits) in (12, 13) and digits.startswith("55") else None


def score_class(score) -> str:
    if score is None:
        return "score-none"
    return "score-good" if score >= 75 else "score-mid" if score >= 50 else "score-bad"


def fipe_class(p) -> str:
    if p is None:
        return ""
    return "pos" if p <= -3 else "neg" if p >= 5 else "neutral"


templates.env.filters.update(brl=brl, km=km, pct=pct, dt=dt, ago=ago, num=num,
                             score_class=score_class, fipe_class=fipe_class, days_since=days_since,
                             wa_phone=wa_phone)
templates.env.tests["new_vehicle"] = is_new
templates.env.globals.update(STAGES=list(Stage), STAGE_LABELS=STAGE_LABELS,
                             SOURCE_LABELS={"olx": "OLX", "webmotors": "Webmotors", "socarrao": "SóCarrão"},
                             TRANSMISSION={"automatico": "Automático", "manual": "Manual"})
