"""Резолв username из БД: часто без «@»; при «нет пользователя» пробуем с префиксом @."""
from __future__ import annotations

from telethon import TelegramClient
from telethon.errors import RPCError, UsernameInvalidError, UsernameNotOccupiedError


def is_username_missing_telegram_error(exc: BaseException) -> bool:
    """Нет такого @username в Telegram (No user has…, UsernameNotOccupied и т.д.)."""
    if isinstance(exc, (UsernameNotOccupiedError, UsernameInvalidError)):
        return True
    if isinstance(exc, RPCError):
        code = str(getattr(exc, "message", "") or "").upper().replace(" ", "_")
        if code in ("USERNAME_NOT_OCCUPIED", "USERNAME_INVALID"):
            return True
    es = (str(exc) or "").lower()
    if not es:
        return False
    needles = (
        "no user has",
        "nobody is using",
        "cannot find any entity",
        "could not find the input entity",
        "could not find any entity",
        "username is not occupied",
        "username invalid",
        "not in use by anyone",
        "no user found",
    )
    return any(n in es for n in needles)


async def get_entity_username_try_at_prefix(
    client: TelegramClient, username: str
):
    """
    1) get_entity(slug) — slug без ведущего @, как в БД.
    2) Только если ошибка означает «username не найден / невалиден» — get_entity(@slug).
    3) Если и второй раз не найден — пробрасываем последнее исключение (рассылка пометит в БД).
    Сетевые ошибки и FloodWait после первой попытки не маскируем второй попыткой.
    """
    slug = (username or "").strip().lstrip("@")
    if not slug:
        raise ValueError("empty username")

    first_exc: BaseException | None = None
    try:
        return await client.get_entity(slug)
    except Exception as e:
        first_exc = e
        if not is_username_missing_telegram_error(e):
            raise

    at_key = f"@{slug}"
    try:
        return await client.get_entity(at_key)
    except Exception as e2:
        # Финальная ошибка уходит наружу → skip + mark_username_not_found в runner.
        raise e2 from first_exc
