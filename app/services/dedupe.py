"""Cross-source duplicate detection: the same car on OLX and Webmotors → one Vehicle."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import Listing, Vehicle
from app.services.normalize import norm

log = logging.getLogger(__name__)


def shingles(text: str, k: int = 3) -> set[str]:
    words = norm(text).split()
    return {" ".join(words[i:i + k]) for i in range(max(0, len(words) - k + 1))}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def dhash(image_bytes: bytes, size: int = 8) -> str:
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((size + 1, size))
    px = img.tobytes()
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


_SELLER_NOISE = {"veiculos", "veiculo", "multimarcas", "multimarca", "automoveis", "auto", "car", "cars",
                 "motors", "ltda", "me", "eireli", "comercio", "de", "e"}


def _seller_key(name: str | None) -> str:
    return " ".join(w for w in norm(name).split() if w not in _SELLER_NOISE)


@dataclass
class MatchScore:
    score: float
    reasons: list[str]


def compare(a: Listing, b: Listing) -> MatchScore | None:
    """None = incompatible (different model/year). Otherwise 0..1 confidence."""
    if not (a.brand and a.model and a.brand == b.brand and a.model == b.model):
        return None
    ya, yb = a.year_model or a.year_fab, b.year_model or b.year_fab
    if ya and yb and ya != yb:
        return None
    s, why = 0.2, ["modelo/ano"]
    if a.km and b.km:
        diff = abs(a.km - b.km) / max(a.km, b.km)
        if diff <= 0.01:
            s += 0.3
            why.append("km ≈ igual")
        elif diff <= 0.03:
            s += 0.2
            why.append("km próximo")
        elif diff <= 0.06:
            s += 0.1
        else:
            s -= 0.3
    if a.color and b.color:
        if norm(a.color) == norm(b.color):
            s += 0.15
            why.append("cor")
        else:
            s -= 0.3
    if a.city and b.city:
        if norm(a.city) == norm(b.city):
            s += 0.1
            why.append("cidade")
        else:
            s -= 0.1
    vsim = jaccard(set(norm(a.version).split()), set(norm(b.version).split()))
    s += 0.15 * vsim
    if a.price and b.price:
        pd = abs(a.price - b.price) / max(a.price, b.price)
        if pd <= 0.05:
            s += 0.1
            why.append("preço")
        elif pd <= 0.12:
            s += 0.05
        elif pd > 0.15:
            s -= 0.15
    sa, sb = _seller_key(a.seller_name), _seller_key(b.seller_name)
    if sa and sb:
        if sa == sb or sa in sb or sb in sa:
            s += 0.15
            why.append("mesmo vendedor")
        else:  # two different dealers rarely sell the very same car
            s -= 0.25
    if a.photo_hash and b.photo_hash:
        h = hamming(a.photo_hash, b.photo_hash)
        if h <= 6:
            s += 0.35
            why.append("foto igual")
        elif h <= 12:
            s += 0.15
            why.append("foto parecida")
        elif h > 20:
            s -= 0.1
    dj = jaccard(shingles(a.description), shingles(b.description))
    if dj >= 0.5:
        s += 0.2
        why.append("descrição")
    elif dj >= 0.25:
        s += 0.1
    return MatchScore(max(0.0, min(1.0, round(s, 3))), why)


def candidates(session: Session, listing: Listing) -> list[tuple[Vehicle, MatchScore]]:
    """Vehicles that might be this listing, best first. Same-source vehicles are skipped."""
    q = select(Vehicle).where(Vehicle.brand == listing.brand, Vehicle.model == listing.model)
    if listing.year_model:
        q = q.where(Vehicle.year_model == listing.year_model)
    out = []
    for v in session.exec(q).all():
        if v.id == listing.vehicle_id:
            continue
        others = session.exec(select(Listing).where(Listing.vehicle_id == v.id)).all()
        if any(o.source == listing.source for o in others):
            continue
        best = None
        for o in others:
            ms = compare(listing, o)
            if ms and (best is None or ms.score > best.score):
                best = ms
        if best:
            out.append((v, best))
    out.sort(key=lambda x: -x[1].score)
    return out


def photo_hash_for(client, url: str) -> str | None:
    try:
        resp = client._client.get(url, timeout=15)
        if resp.status_code == 200 and re.match(r"image/", resp.headers.get("content-type", "")):
            return dhash(resp.content)
    except Exception as exc:
        log.info("photo hash failed", extra={"url": url, "err": str(exc)})
    return None
