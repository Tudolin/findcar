"""Listing score 0–100 with an itemized explanation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.timeutil import utcnow
from app.models import ModelSpec, RedFlagRule, Vehicle
from app.services.normalize import norm

LABELS = {
    "price_fipe": "Preço vs FIPE",
    "km_year": "Km por ano",
    "age": "Idade",
    "transmission": "Câmbio",
    "seller": "Vendedor",
    "consumption": "Consumo",
}


def _lerp(x: float, x0: float, x1: float) -> float:
    """1.0 at x0, 0.0 at x1 (clamped)."""
    if x0 == x1:
        return 1.0
    return max(0.0, min(1.0, (x1 - x) / (x1 - x0)))


@dataclass
class ScoreResult:
    score: int
    breakdown: list[dict]
    flags: list[dict]


def inferred_flags(v: Vehicle, penalties: dict) -> list[dict]:
    flags = []
    model, brand = norm(v.model), norm(v.brand)
    aut = v.transmission == "automatico"
    year = v.year_model or 0
    if aut and brand == "ford" and model in {"fiesta", "focus", "ecosport"} and year >= 2011:
        flags.append({"label": "Câmbio Powershift (histórico de falhas)", "penalty":
                      penalties.get("powershift", 15), "match": "inferido: Ford automático 2011+"})
    if aut and brand in {"peugeot", "citroen"} and year <= 2015:
        flags.append({"label": "Câmbio AL4 (histórico de falhas)", "penalty":
                      penalties.get("al4", 15), "match": "inferido: PSA automático ≤2015"})
    return flags


def match_flags(text: str, rules: list[RedFlagRule]) -> list[dict]:
    flags = []
    for r in rules:
        if not r.enabled:
            continue
        m = re.search(r.pattern, text, re.I)
        if m:
            flags.append({"label": r.label, "penalty": r.penalty, "match": m.group(0)})
    return flags


def compute(v: Vehicle, text: str, rules: list[RedFlagRule], weights: dict,
            seller_values: dict, inferred: dict, spec: ModelSpec | None = None) -> ScoreResult:
    parts: dict[str, tuple[float | None, str]] = {}

    if v.fipe_diff_pct is not None:
        # -20% vs FIPE = perfect, +15% = zero
        parts["price_fipe"] = (_lerp(v.fipe_diff_pct, -20, 15), f"{v.fipe_diff_pct:+.1f}% da FIPE")
    else:
        parts["price_fipe"] = (None, "FIPE indisponível")

    age = max(1, utcnow().year - (v.year_model or utcnow().year) + 1)
    if v.km is not None:
        per_year = v.km / age
        parts["km_year"] = (_lerp(per_year, 8000, 25000), f"{per_year:,.0f} km/ano".replace(",", "."))
    else:
        parts["km_year"] = (None, "km não informado")

    if v.year_model:
        parts["age"] = (_lerp(age, 1, 18), f"{age} ano(s) ({v.year_model})")
    else:
        parts["age"] = (None, "ano não informado")

    t = v.transmission
    parts["transmission"] = (
        {"automatico": 1.0, "manual": 0.4}.get(t or "", 0.5),
        {"automatico": "automático", "manual": "manual"}.get(t or "", "não informado"),
    )

    if v.seller_type:
        parts["seller"] = (float(seller_values.get(v.seller_type, 0.5)), v.seller_type)
    else:
        parts["seller"] = (None, "não informado")

    if spec and spec.consumption_city:
        parts["consumption"] = (_lerp(spec.consumption_city, 13, 8),
                                f"{spec.consumption_city:.1f} km/l cidade (aprox.)")
    else:
        parts["consumption"] = (None, "sem dado de consumo")

    total_w = sum(w for k, w in weights.items() if k in parts and parts[k][0] is not None)
    breakdown, base = [], 0.0
    for key, w in weights.items():
        if key not in parts:
            continue
        value, detail = parts[key]
        if value is None or total_w == 0:
            breakdown.append({"key": key, "label": LABELS.get(key, key), "points": None,
                              "max": None, "detail": detail + " (ignorado)"})
            continue
        maxp = 100 * w / total_w
        pts = maxp * value
        base += pts
        breakdown.append({"key": key, "label": LABELS.get(key, key), "points": round(pts, 1),
                          "max": round(maxp, 1), "detail": detail})

    flags, labels = [], set()
    for f in match_flags(text, rules) + inferred_flags(v, inferred):
        if f["label"] not in labels:  # explicit rule + inferred rule for the same issue
            labels.add(f["label"])
            flags.append(f)
    penalty = sum(f["penalty"] for f in flags)
    for f in flags:
        breakdown.append({"key": "flag", "label": f"⚠ {f['label']}", "points": -f["penalty"],
                          "max": None, "detail": f"“{f['match']}”"})
    return ScoreResult(max(0, min(100, round(base - penalty))), breakdown, flags)
