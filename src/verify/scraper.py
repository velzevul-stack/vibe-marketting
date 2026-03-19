"""Скрапинг сообщений из групп через Telethon."""
import asyncio

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from src.config import Settings
from src.db import get_db
from src.invite.manager import AccountPool
from src.verify.parser import extract_sellers


async def scrape_group(
    group_link: str,
    limit: int = 300,
    pool: "AccountPool | None" = None,
    settings: Settings | None = None,
    on_progress=None,
) -> tuple[int, int]:
    """
    Скрапить группу и сохранить найденных продавцов.
    Возвращает (hot_count, warm_count).
    Распределение по аккаунтам через pool.
    """
    db = get_db()
    acc_pool = pool or AccountPool()
    state = acc_pool.get_best_account()
    if not state:
        return 0, 0
    client = acc_pool.get_client(state.session_name, prefer_pool_for_read=True)
    if not client:
        return 0, 0

    sett = settings or Settings()
    hot_count = 0
    warm_count = 0
    processed = 0

    try:
        await client.connect()
        if not await client.is_user_authorized():
            return 0, 0

        entity = await client.get_entity(group_link)
        chat_id = str(getattr(entity, "id", "") or entity)

        async for message in client.iter_messages(entity, limit=limit):
            processed += 1
            if on_progress:
                on_progress(processed, limit)

            text = message.text or ""
            sender = message.sender
            sender_id = str(sender.id) if sender and hasattr(sender, "id") else None
            sender_username = getattr(sender, "username", None) if sender else None

            sellers = extract_sellers(
                text,
                sender_id=sender_id,
                sender_username=sender_username,
                message_id=message.id,
            )

            for s in sellers:
                uid = s.telegram_id
                uname = f"@{s.username}" if s.username else None
                if await db.user_exists(uid, uname):
                    continue
                added = await db.add_user(
                    telegram_id=uid,
                    username=uname,
                    category=s.category,
                    source_chat_id=chat_id,
                    source_message_id=s.source_message_id,
                    metadata={"matched": s.matched_keywords},
                )
                if added:
                    if s.category == "hot":
                        hot_count += 1
                    else:
                        warm_count += 1

            if processed % 50 == 0:
                await db.save_checkpoint(chat_id, message.id, hot_count + warm_count)

        await db.save_checkpoint(chat_id, 0, hot_count + warm_count)
        acc_pool.mark_used(state.session_name)
    except FloodWaitError as e:
        acc_pool.mark_flood_wait(state.session_name, e.seconds)
        await asyncio.sleep(e.seconds)
        return await scrape_group(group_link, limit, acc_pool, settings, on_progress)
    finally:
        await client.disconnect()

    return hot_count, warm_count
