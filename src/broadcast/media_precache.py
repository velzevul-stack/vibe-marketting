"""Один раз загрузить картинки кампании в «Избранное» и получить InputPhoto/InputDocument для повторной отправки."""
from __future__ import annotations

from pathlib import Path

from rich.console import Console
from telethon import TelegramClient, utils
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from src.telethon_flood_wait import flood_wait_seconds, sleep_flood_wait


async def precache_campaign_images(
    client: TelegramClient,
    paths: tuple[Path, Path, Path],
    *,
    max_flood_retries: int = 3,
    console: Console | None = None,
    session_label: str = "",
) -> tuple[object, object, object]:
    """
    Для каждого пути: send_file('me', ...), извлечь дескриптор для send_file(peer, ...).
    Сообщения в Saved Messages не удаляем — стабильнее для повторного использования.
    """
    out: list[object] = []
    for p in paths:
        msg = await _send_to_self_with_flood_retry(
            client,
            p,
            max_flood_retries=max_flood_retries,
            console=console,
            session_label=session_label or "precache",
        )
        media = msg.media
        if isinstance(media, MessageMediaPhoto):
            out.append(utils.get_input_photo(media.photo))
        elif isinstance(media, MessageMediaDocument) and media.document:
            out.append(utils.get_input_document(media.document))
        else:
            raise TypeError(
                f"Неизвестный тип медиа после загрузки {p}: {type(media).__name__}"
            )
    return (out[0], out[1], out[2])


async def _send_to_self_with_flood_retry(
    client: TelegramClient,
    path: Path,
    *,
    max_flood_retries: int,
    console: Console | None,
    session_label: str,
):
    for attempt in range(max_flood_retries + 1):
        try:
            return await client.send_file("me", path, silent=True)
        except FloodWaitError as e:
            if attempt >= max_flood_retries:
                raise
            sec = flood_wait_seconds(e)
            await sleep_flood_wait(
                sec, console=console, session_label=session_label, prefix="FloodWait (precache)"
            )
