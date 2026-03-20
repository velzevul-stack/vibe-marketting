"""Эвристика «признаки Беларуси» по тексту (username + metadata JSON)."""
import functools
import re

from src.config import load_cities

# Явные маркеры и латиница для чатов/ников
_MARKERS = (
    "беларусь",
    "беларус ",
    "беларус,",
    "беларус.",
    "белорус",
    "белорусс",
    "belarus",
    "рб.",
    " рб ",
    " рб)",
    "(рб",
    "🇧🇾",
    "мінск",
    "minsk",
    "grodno",
    "vitebsk",
    "brest",
    "gomel",
    "mogilev",
    "бнр",
    "by_",
    "_by",
)


@functools.lru_cache(maxsize=1)
def _city_needles() -> frozenset[str]:
    needles: set[str] = set()
    for c in load_cities():
        if not (c or "").strip():
            continue
        cl = str(c).strip().lower()
        needles.add(cl)
        if "ё" in cl:
            needles.add(cl.replace("ё", "е"))
    return frozenset(needles)


def text_has_belarus_signals(text: str) -> bool:
    """
    True, если в тексте есть маркеры РБ или название города из cities_by.json
    (как в старом filter_belarus_groups для групп).
    """
    if not (text or "").strip():
        return False
    combined = text.lower()
    for m in _MARKERS:
        if m.lower() in combined:
            return True
    for city in _city_needles():
        if len(city) >= 4 and city in combined:
            return True
        if len(city) <= 3 and re.search(
            rf"(?<![а-яёa-z]){re.escape(city)}(?![а-яёa-z])", combined
        ):
            return True
    return False


def user_row_matches_belarus(username: str | None, metadata_json: str | None) -> bool:
    """Строка пользователя БД: ник + сырой JSON metadata."""
    u = (username or "").strip()
    m = (metadata_json or "").strip()
    return text_has_belarus_signals(f"{u} {m}")
