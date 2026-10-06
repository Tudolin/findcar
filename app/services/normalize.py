"""Brand/model/version normalization via the alias table."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import Alias


def norm(text: str | None) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9.]+", " ", t)
    t = re.sub(r"(?<![0-9])\.|\.(?![0-9])", " ", t)  # keep "1.5", drop other dots
    return re.sub(r"\s+", " ", t).strip()


def is_automatic(transmission: str | None) -> bool:
    return normalize_transmission(transmission) == "automatico"


def normalize_transmission(raw: str | None) -> str | None:
    t = norm(raw)
    if not t:
        return None
    if re.search(r"\b(manual|mec|mecanico)\b", t):
        return "manual"
    if re.search(r"\b(aut|auto|automatico|automatica|automatizado|cvt|powershift|dualogic|tiptronic|at)\b", t):
        return "automatico"
    return None


def normalize_seller(raw: str | None) -> str | None:
    t = norm(raw)
    if not t:
        return None
    if t in {"pj", "loja", "profissional", "concessionaria", "revenda"} or "loja" in t:
        return "loja"
    if t in {"pf", "particular", "pessoa fisica"}:
        return "particular"
    return None


def title_case(text: str) -> str:
    keep_upper = {"ex", "exl", "lx", "lxl", "dx", "cx", "sel", "se", "s", "gl", "glx", "flex",
                  "16v", "8v", "cvt", "at", "mt", "gsr", "hb20", "hb20s", "hb20x", "v6"}
    words = []
    for w in text.split():
        lw = w.lower()
        if lw in keep_upper or re.fullmatch(r"[0-9.]+[a-z]?", lw):
            words.append(w.upper())
        else:
            words.append(w.capitalize())
    return " ".join(words)


@dataclass
class Canonical:
    brand: str | None
    model: str | None
    version: str | None


class Normalizer:
    """Loads aliases once and resolves raw strings to canonical names."""

    def __init__(self, aliases: list[Alias]):
        self.brands = [(norm(a.pattern), a.canonical) for a in aliases if a.kind == "brand"]
        self.models = [
            (norm(a.pattern), a.canonical, a.brand) for a in aliases if a.kind == "model"
        ]
        self.versions = [
            (norm(a.pattern), a.canonical, a.brand, a.model)
            for a in aliases
            if a.kind == "version"
        ]
        # Longest pattern first so "hb20s" wins over "hb20".
        self.brands.sort(key=lambda x: -len(x[0]))
        self.models.sort(key=lambda x: -len(x[0]))
        self.versions.sort(key=lambda x: -len(x[0]))

    @classmethod
    def from_db(cls, session: Session) -> Normalizer:
        return cls(list(session.exec(select(Alias)).all()))

    @staticmethod
    def _find(pattern: str, text: str) -> re.Match | None:
        return re.search(rf"(?<![a-z0-9]){re.escape(pattern)}(?![a-z0-9])", text)

    def resolve(self, brand: str | None, model: str | None, version: str | None,
                title: str = "") -> Canonical:
        nbrand = norm(brand)
        canon_brand = None
        for pat, canon in self.brands:
            if pat == nbrand or self._find(pat, nbrand) or (not nbrand and self._find(pat, norm(title))):
                canon_brand = canon
                break
        if canon_brand is None and brand:
            canon_brand = brand.strip().title()

        haystack = " ".join(filter(None, [norm(model), norm(version), norm(title)]))
        canon_model, rest = None, norm(version) or ""
        for pat, canon, scope in self.models:
            if scope and canon_brand and scope != canon_brand:
                continue
            m = self._find(pat, haystack)
            if m:
                canon_model = canon
                # version = what's left of the model+version string after the model name
                full = " ".join(filter(None, [norm(model), norm(version)])) or norm(title)
                mm = self._find(pat, full)
                rest = full[mm.end():].strip() if mm else rest
                if canon_brand and norm(canon_brand) in rest.split()[:1]:
                    rest = rest.split(" ", 1)[1] if " " in rest else ""
                break
        if canon_model is None and model:
            canon_model = model.strip().title()

        canon_version = self.resolve_version(canon_brand, canon_model, rest)
        return Canonical(canon_brand, canon_model, canon_version)

    def resolve_version(self, brand: str | None, model: str | None, rest: str) -> str | None:
        if not rest:
            return None
        text = rest
        for pat, canon, sb, sm in self.versions:
            if (sb and sb != brand) or (sm and sm != model):
                continue
            if self._find(pat, text):
                text = re.sub(rf"(?<![a-z0-9]){re.escape(pat)}(?![a-z0-9])", norm(canon), text)
        return title_case(text) or None
