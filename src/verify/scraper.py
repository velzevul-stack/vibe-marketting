"""Скрапинг сообщений из групп через Telethon."""
import asyncio
import re

from rich.console import Console
from rich.markup import escape
from telethon import TelegramClient
from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)

from src.config import Settings
from src.db import get_db
from src.invite.manager import AccountPool
from src.verify.parser import extract_sellers

_console = Console()

# Публичный @username Telegram: буква/цифра/_, обычно от 5 символов; допускаем 4 для старых ников.
_USERNAME_SLUG = re.compile(r"^[A-Za-z0-9_]{4,}$")


def normalize_scrape_target(link: str | None, id_fallback: str | None = None) -> str | None:
    """
    Привести ссылку из found_groups.json / txt к виду, понятному get_entity.
    Если в ``link`` нет t.me, но ``id_fallback`` похож на username — собрать https://t.me/...
    """
    raw = (link or "").strip()
    fb = (str(id_fallback or "").strip().lstrip("@"))
    low = raw.lower()

    if "joinchat/" in low:
        return raw if raw.startswith("http") else f"https://{raw.lstrip('/')}"

    if "t.me/+" in low or "telegram.me/+" in low:
        s = raw.replace(" ", "")
        return s if s.startswith("http") else f"https://{s.lstrip('/')}"

    if re.search(r"t\.me/c/\d+", low):
        return raw if raw.startswith("http") else f"https://{raw.lstrip('/')}"

    if "t.me/" in low or "telegram.me/" in low:
        return raw if raw.startswith("http") else f"https://{raw.lstrip('/')}"

    if raw.startswith("@"):
        u = raw[1:].strip()
        if _USERNAME_SLUG.match(u):
            return f"https://t.me/{u}"
        return None

    if raw and _USERNAME_SLUG.match(raw):
        return f"https://t.me/{raw}"

    if fb and _USERNAME_SLUG.match(fb):
        return f"https://t.me/{fb}"

    return raw if raw else None


def _friendly_entity_error(exc: BaseException, ref: str) -> ValueError:
    if isinstance(exc, ChannelPrivateError):
        hint = (
            "Чат закрытый — этот аккаунт не состоит в группе. Сначала главное меню → "
            "п.3 «Вступить в группы», затем снова сбор."
        )
    else:
        hint = (
            "По ссылке нет такого публичного чата в Telegram: @username мог переименоваться, "
            "канал удалён, или запись в found_groups.json / поиске устарела. "
            "Проверьте ссылку в браузере; обновите п.1 «Поиск групп» или config/group_links.txt."
        )
    return ValueError(f"{hint} Ссылка: {ref!s} — {exc}")


async def scrape_group(
    group_link: str,
    limit: int = 300,
    pool: "AccountPool | None" = None,
    settings: Settings | None = None,
    on_progress=None,
    client: TelegramClient | None = None,
    id_fallback: str | None = None,
) -> tuple[int, int]:
    """
    Скрапить группу и сохранить найденных продавцов.
    Возвращает (hot_count, warm_count).
    Если передан ``client`` — используется он (без disconnect в конце); pool/state опциональны.
    ``id_fallback`` — поле ``id`` из JSON, если ``link`` пустой или без t.me.
    """
    sett = settings or Settings()
    db = get_db()
    own_client = client is None
    state = None
    session_for_flood: str | None = None
    acc_pool: AccountPool | None

    ref = normalize_scrape_target(group_link, id_fallback)
    if not ref:
        return 0, 0

    if client is None:
        acc_pool = pool or AccountPool()
        pinned = (sett.scrape_session_name or "").strip()
        if pinned:
            state = acc_pool.account_state_by_name(pinned)
        else:
            state = acc_pool.get_best_account()
        if not state:
            return 0, 0
        session_for_flood = state.session_name
        client = acc_pool.get_client(
            state.session_name, prefer_pool_for_read=True, settings=sett
        )
        if not client:
            return 0, 0
    else:
        acc_pool = pool

    hot_count = 0
    warm_count = 0
    external_client = client is not None and not own_client

    try:
        while True:
            try:
                if external_client:
                    _console.print(f"[dim]  → Подключение к Telegram…[/] [cyan]{escape(ref)}[/]")
                await client.connect()
                if not await client.is_user_authorized():
                    if external_client:
                        _console.print("[red]  → Сессия не авторизована, сбор пропущен.[/]")
                    return 0, 0

                if external_client:
                    _console.print("[dim]  → Запрос чата (get_entity), подождите…[/]")
                entity = await client.get_entity(ref)
                chat_id = str(getattr(entity, "id", "") or entity)
                chat_label = (
                    getattr(entity, "title", None)
                    or getattr(entity, "username", None)
                    or chat_id
                )
                if external_client:
                    _console.print(
                        f"[dim]  → Чат:[/] [white]{escape(str(chat_label))}[/] "
                        f"[dim]· чтение до {limit} сообщений (первое сообщение с сервера может идти долго)…[/]"
                    )
                processed = 0

                async for message in client.iter_messages(entity, limit=limit):
                    processed += 1
                    if on_progress:
                        on_progress(processed, limit)
                    if external_client and not on_progress and (
                        processed == 1 or processed % 50 == 0
                    ):
                        _console.print(
                            f"[dim]  … сообщений обработано:[/] [cyan]{processed}[/][dim]/{limit}[/]"
                        )

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

                    if sett.delay_scrape_per_message > 0:
                        await asyncio.sleep(sett.delay_scrape_per_message)

                await db.save_checkpoint(chat_id, 0, hot_count + warm_count)
                if state is not None and acc_pool is not None:
                    acc_pool.mark_used(state.session_name)
                break
            except FloodWaitError as e:
                if acc_pool is not None and session_for_flood:
                    acc_pool.mark_flood_wait(session_for_flood, e.seconds)
                sess = escape(str(session_for_flood)) if session_for_flood else "отдельный вход"
                _console.print(
                    f"\n[yellow]FloodWait:[/] пауза [bold]{e.seconds}[/] с "
                    f"([dim]сессия[/] [cyan]{sess}[/])"
                )
                await asyncio.sleep(e.seconds)
                continue
            except (
                UsernameNotOccupiedError,
                UsernameInvalidError,
                ChannelInvalidError,
                ChannelPrivateError,
                InviteHashInvalidError,
                InviteHashExpiredError,
            ) as e:
                raise _friendly_entity_error(e, ref) from e
            except ValueError as e:
                es = str(e)
                if "No user has" in es or "Cannot find any entity" in es or "Nobody is using" in es:
                    raise _friendly_entity_error(e, ref) from e
                raise
    finally:
        if own_client:
            try:
                await client.disconnect()
            except Exception:
                pass

    return hot_count, warm_count
