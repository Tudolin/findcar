"""Helpers to read JSON embedded by Next.js (`<script id="__NEXT_DATA__">`)."""

import json
import re
from collections.abc import Callable, Iterator

_NEXT_RE = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def extract_next_data(html: str) -> dict | None:
    m = _NEXT_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def iter_dict_lists(obj, depth: int = 0) -> Iterator[list[dict]]:
    if depth > 12:
        return
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            yield obj
        for x in obj:
            yield from iter_dict_lists(x, depth + 1)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from iter_dict_lists(v, depth + 1)


def find_listing_array(obj, is_item: Callable[[dict], bool]) -> list[dict]:
    """Largest list whose elements look like listings (robust to path changes)."""
    best: list[dict] = []
    for lst in iter_dict_lists(obj):
        hits = [x for x in lst if is_item(x)]
        if len(hits) > len(best):
            best = hits
    return best


def ci_get(d, path: str, default=None):
    """Case-insensitive dotted getter: ci_get(x, 'Specification.Make.Value')."""
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return default
        lowered = {str(k).lower(): v for k, v in cur.items()}
        cur = lowered.get(part.lower())
        if cur is None:
            return default
    return cur


def first(d, *paths, default=None):
    for p in paths:
        v = ci_get(d, p)
        if v not in (None, "", [], {}):
            return v
    return default
