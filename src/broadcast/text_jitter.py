"""Случайная замена части кириллических букв на латиницу в подписи к медиа."""
from __future__ import annotations

import random
import re

from src.config import Settings

# е U+0435, а U+0430, о U+043E (нижний регистр в таблице; в тексте как есть)
_HOMOGLYPHS = {
    "\u0435": "e",
    "\u0430": "a",
    "\u043e": "o",
}

# Не латинизировать упоминания и ссылки (подстроки целиком).
_PROTECTED_RES = (
    re.compile(r"@nicotinecrm_bot", re.IGNORECASE),
    re.compile(r"@nicotine_crm_admin", re.IGNORECASE),
    re.compile(
        r"(?:https?://[^\s<>\[\]()\"']+|"
        r"t\.me/[^\s<>\[\]()\"']+|"
        r"telegram\.me/[^\s<>\[\]()\"']+|"
        r"tg://[^\s<>\[\]()\"']+|"
        r"www\.[^\s<>\[\]()\"']+)",
        re.IGNORECASE,
    ),
)


def _merged_protected_spans(text: str) -> list[tuple[int, int]]:
    raw: list[tuple[int, int]] = []
    for rx in _PROTECTED_RES:
        for m in rx.finditer(text):
            raw.append((m.start(), m.end()))
    if not raw:
        return []
    raw.sort(key=lambda x: x[0])
    merged: list[list[int]] = [[raw[0][0], raw[0][1]]]
    for s, e in raw[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e:
            merged[-1][1] = max(last_e, e)
        else:
            merged.append([s, e])
    return [(a, b) for a, b in merged]


def _index_in_spans(i: int, spans: list[tuple[int, int]]) -> bool:
    for s, e in spans:
        if s <= i < e:
            return True
    return False


def apply_caption_homoglyph(text: str, settings: Settings) -> str:
    if not text or not settings.broadcast_homoglyph_enabled:
        return text
    p = max(0.0, min(1.0, float(settings.broadcast_homoglyph_probability)))
    if p <= 0:
        return text
    spans = _merged_protected_spans(text)
    out: list[str] = []
    for i, ch in enumerate(text):
        if _index_in_spans(i, spans):
            out.append(ch)
            continue
        rep = _HOMOGLYPHS.get(ch)
        if rep is not None and random.random() < p:
            out.append(rep)
        else:
            out.append(ch)
    return "".join(out)
