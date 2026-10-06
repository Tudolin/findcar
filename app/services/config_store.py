"""Key/value settings editable from the UI, with code defaults."""

import copy

from sqlmodel import Session

from app.models import AppSetting

DEFAULTS: dict = {
    "score.weights": {
        "price_fipe": 35,
        "km_year": 20,
        "age": 10,
        "transmission": 15,
        "seller": 5,
        "consumption": 5,
    },
    "score.seller_values": {"particular": 1.0, "loja": 0.8},
    "score.inferred_flags": {"powershift": 15, "al4": 15},
    "inactive_after_runs": 3,
    "alerts": {
        "min_score": 70,
        "drop_pct": 5.0,
        "drop_abs": 1500,
        "favorite_inactive": True,
        "daily_summary": True,
        "daily_summary_time": "20:00",
    },
    "schedule": {"times": ["08:10", "13:10", "19:10"], "fipe_refresh_days": 15},
    "dedupe": {"auto_threshold": 0.75, "suggest_threshold": 0.5, "photo_hash": True},
    "details": {"max_per_run": 25},
}


def get_setting(session: Session, key: str):
    row = session.get(AppSetting, key)
    default = copy.deepcopy(DEFAULTS.get(key))
    if row is None or row.value is None:
        return default
    if isinstance(default, dict) and isinstance(row.value, dict):
        return {**default, **row.value}
    return row.value


def set_setting(session: Session, key: str, value) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
        session.add(row)
