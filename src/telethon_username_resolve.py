"""Резолв username из БД: часто без «@»; при «нет пользователя» пробуем с префиксом @."""
from __future__ import annotations

from telethon import TelegramClient
from telethon.errors import UsernameInvalidError, UsernameNotOccupiedError


def is_username_missing_telegram_error(exc: BaseException) -> bool:
    """Нет такого @username в Telegram (No user has…, UsernameNotOccupied и т.д.)."""
    if isinstance(exc, (UsernameNotOccupiedError, UsernameInvalidError)):
        return True
    es = (str(exc) or "").lower()
    if not es:
        return False
    needles = (
        "no user has",
        "nobody is using",
        "cannot find any entity",
        "username is not occupied",
        "username invalid",
    )
    return any(n in es for n in needles)


async def get_entity_username_try_at_prefix(
    client: TelegramClient, username: str
):
    """
    Сначала get_entity(slug без ведущего @), при ошибке «username не найден» — get_entity(@slug).
    Иначе пробрасываем исходное исключение (сеть, флуд и т.д.).
    """
    slug = (username or "").strip().lstrip("@")
    if not slug:
        raise ValueError("empty username")
    try:
        return await client.get_entity(slug)
    except Exception as e:
        if not is_username_missing_telegram_error(e):
            raise
    return await client.get_entity(f"@{slug}")
