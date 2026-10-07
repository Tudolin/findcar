"""Decoder for Nuxt 3's `<script id="__NUXT_DATA__">` payload (the `devalue` format).

The payload is a flat JSON array; objects and arrays hold *indices* into that array instead
of values, and a few tagged arrays wrap special types (["Reactive", i], ["Date", "…"]…).
"""

from __future__ import annotations

import json
import re

_NUXT_RE = re.compile(r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef"}
_SPECIAL = {-1: None, -2: None, -3: float("nan"), -4: float("inf"), -5: float("-inf"), -6: 0}


def decode(payload: list):
    cache: dict[int, object] = {}

    def resolve(i):
        if isinstance(i, int) and i < 0:
            return _SPECIAL.get(i)
        if i in cache:
            return cache[i]
        v = payload[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str) and len(v) >= 1 and not _is_index_list(v):
                tag = v[0]
                if tag in _WRAPPERS:
                    out = resolve(v[1]) if len(v) > 1 else None
                elif tag == "Date":
                    out = v[1]
                elif tag in ("Set",):
                    out = [resolve(x) for x in v[1:]]
                elif tag == "Map":
                    out = {resolve(v[k]): resolve(v[k + 1]) for k in range(1, len(v) - 1, 2)}
                elif tag == "null":
                    out = {}
                else:
                    out = [resolve(x) for x in v[1:]]
                cache[i] = out
                return out
            out_list: list = []
            cache[i] = out_list
            out_list.extend(resolve(x) for x in v)
            return out_list
        if isinstance(v, dict):
            out_dict: dict = {}
            cache[i] = out_dict
            for k, x in v.items():
                out_dict[k] = resolve(x)
            return out_dict
        cache[i] = v
        return v

    return resolve(0)


def _is_index_list(v: list) -> bool:
    return all(isinstance(x, int) for x in v)


def extract_nuxt_data(html: str):
    m = _NUXT_RE.search(html)
    if not m:
        return None
    try:
        return decode(json.loads(m.group(1)))
    except (json.JSONDecodeError, IndexError, RecursionError):
        return None
