"""Парсинг сообщений и извлечение продавцов."""
import re
from dataclasses import dataclass

from src.config import load_keywords


@dataclass
class SellerMatch:
    """Найденный продавец."""
    telegram_id: str | None
    username: str | None
    category: str  # hot | warm
    source_message_id: int
    matched_keywords: list[str]


def _normalize(text: str) -> str:
    return (text or "").lower()


def _count_matches(text: str, keywords: list[str]) -> list[str]:
    """Найти совпадения по ключевым словам."""
    normalized = _normalize(text)
    return [k for k in keywords if k.lower() in normalized]


def _has_table(text: str) -> bool:
    """Проверить наличие таблицы/Google Sheets."""
    if not text:
        return False
    t = text.lower()
    if "docs.google.com/spreadsheets" in t:
        return True
    if "таблица" in t and ("онлайн" in t or "наличи" in t or "ассортимент" in t):
        return True
    return False


def _extract_usernames(text: str) -> list[str]:
    """Извлечь @username из текста."""
    return re.findall(r"@([a-zA-Z][a-zA-Z0-9_]{4,31})", text or "")


def parse_message(
    text: str,
    sender_id: str | None = None,
    sender_username: str | None = None,
    message_id: int = 0,
) -> SellerMatch | None:
    """
    Разобрать сообщение и определить, является ли автор продавцом жидкостей.
    Возвращает SellerMatch если 2+ совпадения по ключевым словам, иначе None.
    """
    keywords_cfg = load_keywords()
    keywords = keywords_cfg.get("vape_markers", [])
    if not text or not keywords:
        return None

    matches = _count_matches(text, keywords)
    if len(matches) < 2:
        return None

    has_table = _has_table(text)
    category = "hot" if has_table else "warm"

    usernames = _extract_usernames(text)
    # Если автор — бот/канал, берём username из текста
    uid = str(sender_id) if sender_id else None
    uname = sender_username
    if not uname and usernames:
        uname = usernames[0]
    if not uid and not uname:
        return None

    return SellerMatch(
        telegram_id=uid,
        username=uname,
        category=category,
        source_message_id=message_id,
        matched_keywords=matches,
    )


def extract_sellers(
    text: str,
    sender_id: str | None = None,
    sender_username: str | None = None,
    message_id: int = 0,
) -> list[SellerMatch]:
    """
    Извлечь всех продавцов из сообщения.
    Если в тексте несколько @username — возвращаем по одному на каждого.
    """
    match = parse_message(text, sender_id, sender_username, message_id)
    if not match:
        return []

    usernames = _extract_usernames(text)
    if len(usernames) > 1:
        return [
            SellerMatch(
                telegram_id=match.telegram_id,
                username=u,
                category=match.category,
                source_message_id=match.source_message_id,
                matched_keywords=match.matched_keywords,
            )
            for u in usernames
        ]
    return [match]
