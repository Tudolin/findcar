from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def utcnow() -> datetime:
    """Naive UTC: portable across Postgres and the SQLite used in tests."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC).astimezone(ZoneInfo(get_settings().timezone))
