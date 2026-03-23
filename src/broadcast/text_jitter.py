"""Случайная замена части кириллических букв на латиницу в подписи к медиа."""
from __future__ import annotations

import random

from src.config import Settings

# е U+0435, а U+0430, о U+043E
_HOMOGLYPHS = {
    "\u0435": "e",
    "\u0430": "a",
    "\u043e": "o",
}


def apply_caption_homoglyph(text: str, settings: Settings) -> str:
    if not text or not settings.broadcast_homoglyph_enabled:
        return text
    p = max(0.0, min(1.0, float(settings.broadcast_homoglyph_probability)))
    if p <= 0:
        return text
    out: list[str] = []
    for ch in text:
        rep = _HOMOGLYPHS.get(ch)
        if rep is not None and random.random() < p:
            out.append(rep)
        else:
            out.append(ch)
    return "".join(out)
