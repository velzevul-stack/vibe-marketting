"""Параллельная рассылка ЛС: воркер на аккаунт + общая очередь с релокализацией при флуде/лимитах."""
from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal

from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    InputUserDeactivatedError,
    PeerFloodError,
    PeerIdInvalidError,
    RPCError,
    UserIdInvalidError,
    UserIsBlockedError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    UserPrivacyRestrictedError,
)

from src.broadcast.bundle import CampaignBundle, read_campaign_texts
from src.broadcast.media_precache import precache_campaign_images
from src.broadcast.text_jitter import apply_caption_homoglyph
from src.config import Settings, load_accounts, is_proxy_enabled, telethon_session_file
from src.telethon_flood_wait import flood_wait_seconds, sleep_flood_wait
from src.db.database import Database
from src.invite.manager import AccountPool, smart_delay
from src.telethon_username_resolve import (
    get_entity_username_try_at_prefix,
    is_username_missing_telegram_error,
)

BroadcastMode = Literal["normal", "privacy_retry"]

_DISCONNECT_RECONNECT_ATTEMPTS = 3
_SHUTDOWN = object()
# max(секунды от Telegram, ступень): 1.5h → 3h → 6h → 12h → 24h → 48h (дальше последняя).
# PeerFlood — только ступени лестницы (per-session счётчик peer_flood_round), без секунд из RPC.
_BROADCAST_FLOOD_LADDER_SEC: tuple[int, ...] = (
    5400,
    10800,
    21600,
    43200,
    86400,
    172800,
)


def _broadcast_flood_floor_for_index(idx: int) -> int:
    i = min(max(0, idx), len(_BROADCAST_FLOOD_LADDER_SEC) - 1)
    return int(_BROADCAST_FLOOD_LADDER_SEC[i])


class _DegradedExit(BaseException):
    """Не Exception: воркер ушёл в degraded до конца рассылки (не ловить как relay)."""


def _err_tag(exc: BaseException) -> str:
    return type(exc).__name__


def _err_detail(exc: BaseException, limit: int = 120) -> str:
    s = (str(exc) or "").strip() or repr(exc)
    return escape(s[:limit])


def _user_stable_hash(u: dict) -> int:
    uid = u.get("id")
    if uid is not None:
        try:
            return int(uid)
        except (TypeError, ValueError):
            pass
    return hash(str(u.get("telegram_id") or u.get("username") or ""))


def _is_telethon_disconnected_error(exc: BaseException) -> bool:
    """Клиент Telethon отвалился (нельзя слать запросы)."""
    if isinstance(
        exc,
        (
            BrokenPipeError,
            ConnectionResetError,
            ConnectionAbortedError,
            ConnectionError,
        ),
    ):
        return True
    msg = str(exc).lower()
    return (
        "cannot send requests while disconnected" in msg
        or ("disconnected" in msg and "send" in msg)
    )


async def _resolve_peer(client: TelegramClient, u: dict):
    """
    Сначала по числовому telegram_id; при любой ошибке резолва — fallback на username
    (часто в БД лежит битый/чужой id, а @username валиден).
    """
    un_raw = (u.get("username") or "").strip()
    tid_s = str(u.get("telegram_id") or "").strip()

    if tid_s.isdigit():
        try:
            return await client.get_entity(int(tid_s))
        except Exception:
            if un_raw:
                return await get_entity_username_try_at_prefix(client, un_raw)
            raise
    if un_raw:
        return await get_entity_username_try_at_prefix(client, un_raw)
    return None


@dataclass(frozen=True)
class _WorkItem:
    user: dict
    tried: frozenset[str]


@dataclass
class BroadcastTotals:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    privacy_skipped: int = 0
    deferred_daily_cap: int = 0
    relay_exhausted: int = 0
    username_not_found_marked: int = 0
    by_session: dict[str, tuple[int, int, int, int, int, int]] = field(default_factory=dict)
    # sent, failed, skipped, privacy, daily_cap, relay_exhausted


async def run_dm_broadcast(
    *,
    bundle: CampaignBundle,
    db: Database,
    settings: Settings,
    console: Console,
    category: str | None,
    total_limit: int,
    exclude_invited: bool = True,
    broadcast_mode: BroadcastMode = "normal",
    send_media: bool = True,
) -> BroadcastTotals:
    """
    Общая asyncio.Queue получателей; при флуде/лимите суток и т.п. задача возвращается в пул
    для других аккаунтов (лимит разных аккаунтов на пользователя — в settings).
    broadcast_mode=privacy_retry — только очередь после UserPrivacyRestricted.
    send_media=False — только текст (send_message), без фото и прекэша.
    """
    await db.init()
    texts = read_campaign_texts(bundle)
    images = bundle.image_paths

    accs = load_accounts()
    if not accs:
        console.print("[red]Нет аккаунтов в accounts.json (импортируйте ZIP из пакета).[/]")
        return BroadcastTotals()

    exclude_privacy = broadcast_mode == "normal"
    only_privacy = broadcast_mode == "privacy_retry"

    users = await db.get_users(
        category=category,
        limit=max(1, total_limit),
        exclude_invited=exclude_invited,
        exclude_added_to_contacts=False,
        exclude_broadcast=True,
        exclude_privacy_blocked=exclude_privacy,
        only_privacy_retry=only_privacy,
        exclude_username_not_found=True,
    )
    if not users:
        console.print("[yellow]Нет получателей (категория / лимит / фильтры рассылки).[/]")
        return BroadcastTotals()

    pool = AccountPool()
    session_names = [a.get("session_name") for a in accs if a.get("session_name")]
    session_names = [s for s in session_names if s]
    if not session_names:
        console.print("[red]Нет session_name в accounts.json.[/]")
        return BroadcastTotals()

    _names_before = len(session_names)
    session_names = list(dict.fromkeys(session_names))
    if len(session_names) != _names_before:
        console.print(
            "[yellow]В accounts.json были повторы session_name — оставлены уникальные "
            "(один файл .session нельзя открывать двумя воркерами сразу).[/]"
        )

    daily_limit = int(settings.broadcast_daily_limit_per_account)
    if daily_limit < 0:
        daily_limit = 0

    max_attempts = int(settings.broadcast_max_account_attempts_per_user)
    retire_after = int(settings.broadcast_retire_session_after_peer_floods)

    n_acc = len(session_names)
    work_q: asyncio.Queue = asyncio.Queue()
    for u in users:
        await work_q.put(_WorkItem(user=u, tried=frozenset()))

    totals = BroadcastTotals()
    for sn in session_names:
        totals.by_session[sn] = (0, 0, 0, 0, 0, 0)

    totals_lock = asyncio.Lock()
    log_lock = asyncio.Lock()
    prog_lock = asyncio.Lock()
    state_lock = asyncio.Lock()
    pending_lock = asyncio.Lock()
    pending = len(users)
    retired: set[str] = set()
    peer_floods: dict[str, int] = {}
    flood_round: dict[str, int] = {}
    peer_flood_round: dict[str, int] = {}
    total_tasks = len(users)
    session_connect_locks: dict[str, asyncio.Lock] = {}
    account_status: dict[str, str] = {sn: "ожидание старта" for sn in session_names}
    status_lock = asyncio.Lock()

    async def _set_worker_status(sn: str, msg: str) -> None:
        async with status_lock:
            account_status[sn] = msg

    async def _next_broadcast_flood_wait_seconds(sn: str, exc: BaseException) -> int:
        async with state_lock:
            idx = flood_round.get(sn, 0)
            flood_round[sn] = idx + 1
        tg = flood_wait_seconds(exc)
        return max(tg, _broadcast_flood_floor_for_index(idx))

    async def _next_peer_flood_wait_seconds(sn: str) -> tuple[int, int]:
        """Ступень лестницы для PeerFlood; возвращает (секунды, номер раунда 1-based)."""
        async with state_lock:
            idx = peer_flood_round.get(sn, 0)
            peer_flood_round[sn] = idx + 1
        return _broadcast_flood_floor_for_index(idx), idx + 1

    def _session_connect_lock(session_name: str) -> asyncio.Lock:
        key = str(telethon_session_file(session_name, settings).resolve())
        if key not in session_connect_locks:
            session_connect_locks[key] = asyncio.Lock()
        return session_connect_locks[key]

    async def _log(msg: str) -> None:
        async with log_lock:
            console.print(msg)

    async def _bump_task(progress: Progress, task_id: int) -> None:
        async with prog_lock:
            progress.advance(task_id, 1)

    async def _add_totals(
        sn: str,
        *,
        sent: int = 0,
        failed: int = 0,
        skipped: int = 0,
        privacy: int = 0,
        dailycap: int = 0,
        relay: int = 0,
        username_nf: int = 0,
    ) -> None:
        async with totals_lock:
            totals.sent += sent
            totals.failed += failed
            totals.skipped += skipped
            totals.privacy_skipped += privacy
            totals.deferred_daily_cap += dailycap
            totals.relay_exhausted += relay
            totals.username_not_found_marked += username_nf
            s, f, sk, pr, dc, rx = totals.by_session[sn]
            totals.by_session[sn] = (
                s + sent,
                f + failed,
                sk + skipped,
                pr + privacy,
                dc + dailycap,
                rx + relay,
            )

    async def _eligible_sessions(tried: frozenset[str]) -> list[str]:
        async with state_lock:
            return [s for s in session_names if s not in tried and s not in retired]

    async def _finalize_user(progress: Progress, task_id: int) -> None:
        nonlocal pending
        shutdown = False
        async with pending_lock:
            pending -= 1
            if pending == 0:
                shutdown = True
        await _bump_task(progress, task_id)
        if shutdown:
            for _ in range(n_acc):
                await work_q.put(_SHUTDOWN)

    async def _relay_exhausted_log(
        session_name: str,
        u: dict,
        preview: str,
        *,
        detail: str = "",
    ) -> None:
        msg = (
            f"  [red]relay_exhausted[/] [dim]{escape(session_name)}[/] {escape(preview)}"
            + (f" — {detail}" if detail else "")
        )
        await _log(msg)
        await _add_totals(session_name, failed=1, relay=1)

    async def _try_requeue(
        u: dict,
        tried: frozenset[str],
        session_name: str,
        preview: str,
        progress: Progress,
        task_id: int,
    ) -> bool:
        """True если вернули в очередь; False если финал relay_exhausted (уже учтён)."""
        new_tried = frozenset(tried | {session_name})
        if len(new_tried) >= max_attempts:
            await _relay_exhausted_log(
                session_name, u, preview, detail="лимит аккаунтов на получателя"
            )
            await _finalize_user(progress, task_id)
            return False
        elig = await _eligible_sessions(new_tried)
        if not elig:
            await _relay_exhausted_log(
                session_name, u, preview, detail="нет доступных аккаунтов"
            )
            await _finalize_user(progress, task_id)
            return False
        await work_q.put(_WorkItem(user=u, tried=new_tried))
        return True

    async def _record_peer_flood_and_maybe_retire(session_name: str) -> bool:
        """True если сессию вывели из прогона (retired)."""
        async with state_lock:
            peer_floods[session_name] = peer_floods.get(session_name, 0) + 1
            n = peer_floods[session_name]
            if n >= retire_after:
                retired.add(session_name)
                return True
        return False

    async def _degraded_relay_loop(
        session_name: str, progress: Progress, task_id: int
    ) -> None:
        """
        Нет рабочего клиента — перекладываем задачи живым воркерам.
        Если доступных аккаунтов не осталось — финализируем получателя (relay_exhausted).
        """
        while True:
            item = await work_q.get()
            if item is _SHUTDOWN:
                break
            assert isinstance(item, _WorkItem)
            u, tried = item.user, item.tried
            elig = await _eligible_sessions(tried)
            if not elig:
                ident = u.get("username") or u.get("telegram_id") or "?"
                preview = str(ident)[:40]
                await _relay_exhausted_log(
                    session_name,
                    u,
                    preview,
                    detail="нет рабочих аккаунтов для relay",
                )
                await _finalize_user(progress, task_id)
            else:
                await work_q.put(item)
                await asyncio.sleep(random.uniform(0.02, 0.08))

    async def _worker(
        session_name: str, progress: Progress, task_id: int, worker_index: int = 0
    ) -> None:
        client = pool.get_client(session_name, settings=settings)
        if not client:
            await _log(f"[red]{escape(session_name)}[/]: нет клиента (api_id/api_hash)")
            await _set_worker_status(session_name, "нет api, degraded")
            async with state_lock:
                retired.add(session_name)
            await _degraded_relay_loop(session_name, progress, task_id)
            return

        try:
            try:
                await asyncio.sleep(worker_index * 0.1 + random.uniform(0, 0.08))
                await _set_worker_status(session_name, "подключение…")
                async with _session_connect_lock(session_name):
                    await client.connect()
            except Exception as e:
                await _log(
                    f"[red]{escape(session_name)}[/]: подключение — {escape(_err_tag(e))}: "
                    f"{escape(str(e)[:80])}"
                )
                await _set_worker_status(session_name, "ошибка подключения")
                async with state_lock:
                    retired.add(session_name)
                await _degraded_relay_loop(session_name, progress, task_id)
                return

            if not await client.is_user_authorized():
                await _log(f"[red]{escape(session_name)}[/]: сессия не авторизована")
                await _set_worker_status(session_name, "не авторизован")
                async with state_lock:
                    retired.add(session_name)
                await _degraded_relay_loop(session_name, progress, task_id)
                return

            cached_handles: tuple[object, object, object] | None = None
            if send_media and settings.broadcast_precache_media_to_saved:
                await _set_worker_status(session_name, "precache медиа…")
                try:
                    cached_handles, _precache_from_disk = await precache_campaign_images(
                        client,
                        images,
                        console=console,
                        session_label=session_name,
                    )
                    if _precache_from_disk:
                        await _log(
                            f"  [dim]precache[/] [dim]{escape(session_name)}[/]: "
                            "медиа из локального кэша [dim](те же 1–3.jpg, без новой загрузки в Избранное)[/]"
                        )
                    else:
                        await _log(
                            f"  [dim]precache[/] [dim]{escape(session_name)}[/]: "
                            "медиа 1–3.jpg в Избранном; id сохранены для следующего прогона"
                        )
                except Exception as e:
                    await _log(
                        f"  [yellow]precache[/] [dim]{escape(session_name)}[/]: "
                        f"{escape(_err_tag(e))}: {_err_detail(e)} — отправка с диска"
                    )
                    cached_handles = None
            elif send_media:
                cached_handles = None

            while True:
                await _set_worker_status(session_name, "ожидание задачи")
                item = await work_q.get()
                if item is _SHUTDOWN:
                    await _set_worker_status(session_name, "стоп")
                    break
                assert isinstance(item, _WorkItem)
                u, tried = item.user, item.tried

                async with state_lock:
                    sn_retired = session_name in retired
                if sn_retired:
                    elig = await _eligible_sessions(tried)
                    if not elig:
                        ident = u.get("username") or u.get("telegram_id") or "?"
                        preview = str(ident)[:40]
                        await _relay_exhausted_log(
                            session_name,
                            u,
                            preview,
                            detail="сессия в retire, других аккаунтов нет",
                        )
                        await _finalize_user(progress, task_id)
                    else:
                        await work_q.put(item)
                        await asyncio.sleep(random.uniform(0.02, 0.08))
                    continue

                if session_name in tried:
                    await work_q.put(item)
                    await asyncio.sleep(random.uniform(0.02, 0.08))
                    continue

                elig = await _eligible_sessions(tried)
                if not elig:
                    ident = u.get("username") or u.get("telegram_id") or "?"
                    preview = str(ident)[:40]
                    await _relay_exhausted_log(
                        session_name, u, preview, detail="очередь пуста по аккаунтам"
                    )
                    await _finalize_user(progress, task_id)
                    continue

                if session_name not in elig:
                    await work_q.put(item)
                    await asyncio.sleep(random.uniform(0.02, 0.08))
                    continue

                uid = u.get("id")
                h = _user_stable_hash(u)
                raw_caption = texts[h % 2]
                caption = apply_caption_homoglyph(raw_caption, settings)
                img_path = images[h % 3] if send_media else None
                ident = u.get("username") or u.get("telegram_id") or "?"
                preview = str(ident)[:40]

                if daily_limit > 0:
                    n_today = await db.get_broadcast_sent_today_utc(session_name)
                    if n_today >= daily_limit:
                        await _log(
                            f"  [yellow]daily_cap[/] [dim]{escape(session_name)}[/] "
                            f"{escape(preview)} — лимит {daily_limit}/сутки UTC → relay"
                        )
                        await _add_totals(session_name, dailycap=1)
                        await _try_requeue(u, tried, session_name, preview, progress, task_id)
                        continue

                async def _reconnect_client_3x(reason: str) -> bool:
                    for attempt in range(1, _DISCONNECT_RECONNECT_ATTEMPTS + 1):
                        await _log(
                            f"  [yellow]reconnect[/] [dim]{escape(session_name)}[/] "
                            f"{attempt}/{_DISCONNECT_RECONNECT_ATTEMPTS} [dim]{escape(reason[:72])}[/]"
                        )
                        try:
                            async with _session_connect_lock(session_name):
                                try:
                                    await client.disconnect()
                                except Exception:
                                    pass
                                await client.connect()
                            if await client.is_user_authorized():
                                await _log(
                                    f"  [green]reconnect OK[/] [dim]{escape(session_name)}[/]"
                                )
                                return True
                        except Exception as ex:
                            await _log(
                                f"  [dim]reconnect fail[/] [dim]{escape(session_name)}[/]: "
                                f"{escape(_err_tag(ex))}: {escape(str(ex)[:100])}"
                            )
                        await asyncio.sleep(0.4 * attempt)
                    return False

                async def _enter_degraded_after_disconnect(_reason: str) -> None:
                    await _log(
                        f"  [red]disconnect[/] [dim]{escape(session_name)}[/]: "
                        "переподключение исчерпано — retire, ожидание конца рассылки "
                        "[dim](очередь на другие аккаунты)[/]"
                    )
                    await _set_worker_status(session_name, "disconnect → degraded")
                    async with state_lock:
                        retired.add(session_name)
                    await _try_requeue(
                        u, tried, session_name, preview, progress, task_id
                    )
                    await _degraded_relay_loop(session_name, progress, task_id)
                    raise _DegradedExit

                async def _try_send() -> None:
                    peer = await _resolve_peer(client, u)
                    if peer is None:
                        await _log(
                            f"  [yellow]skip[/] [dim]{escape(session_name)}[/] @{escape(preview)} — нет peer"
                        )
                        await _add_totals(session_name, skipped=1)
                        await _finalize_user(progress, task_id)
                        return
                    if send_media:
                        assert img_path is not None
                        media = (
                            cached_handles[h % 3]
                            if cached_handles is not None
                            else img_path
                        )
                        await client.send_file(peer, media, caption=caption)
                    else:
                        await client.send_message(peer, caption)
                    if uid is not None:
                        await db.mark_broadcast_sent(int(uid))
                        await db.increment_broadcast_sent_today_utc(session_name)
                    pool.mark_used(session_name)
                    await _log(
                        f"  [green]OK[/] [dim]{escape(session_name)}[/] → [white]{escape(preview)}[/]"
                    )
                    await _add_totals(session_name, sent=1)
                    await _finalize_user(progress, task_id)

                async def _try_send_resilient() -> None:
                    while True:
                        try:
                            await _try_send()
                            return
                        except (ConnectionError, OSError) as e:
                            if not _is_telethon_disconnected_error(e):
                                raise
                            if not await _reconnect_client_3x(str(e)[:120]):
                                await _enter_degraded_after_disconnect(str(e)[:120])

                async def _peer_flood_after(e: PeerFloodError) -> str:
                    """
                    Возврат: exit_worker — выйти из воркера; continue_outer — след. получатель;
                    done — отправка после паузы успешна.
                    """
                    sec, pf_rnd = await _next_peer_flood_wait_seconds(session_name)
                    pool.mark_flood_wait(session_name, sec)
                    await _set_worker_status(
                        session_name,
                        f"PeerFlood {sec // 60}м ({sec}s)",
                    )
                    await _log(
                        f"  [yellow]peer_flood[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                        f"ожидание [bold]{sec}[/] с [dim](лестница, раунд {pf_rnd})[/], повтор… "
                        f"{_err_detail(e)}"
                    )
                    await sleep_flood_wait(
                        sec,
                        console=console,
                        session_label=session_name,
                        prefix="PeerFlood",
                    )
                    await _set_worker_status(session_name, "активно")
                    try:
                        await _try_send_resilient()
                        return "done"
                    except PeerFloodError as e2:
                        await _log(
                            f"  [yellow]relay[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                            f"PeerFlood после повтора: {_err_detail(e2)}"
                        )
                        if await _record_peer_flood_and_maybe_retire(session_name):
                            await _log(
                                f"  [yellow]retire[/] [dim]{escape(session_name)}[/] "
                                f"после {retire_after} PeerFlood в прогоне"
                            )
                        await _try_requeue(
                            u, tried, session_name, preview, progress, task_id
                        )
                        async with state_lock:
                            if session_name in retired:
                                try:
                                    await client.disconnect()
                                except Exception:
                                    pass
                                await _set_worker_status(session_name, "retired (PeerFlood)")
                                return "exit_worker"
                        return "continue_outer"
                    except Exception as e2:
                        if uid is not None and is_username_missing_telegram_error(e2):
                            await db.mark_username_not_found(int(uid))
                            await _log(
                                f"  [yellow]skip[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                                f"нет @username в TG [dim](после peer_flood)[/]: "
                                f"{escape(_err_tag(e2))}: {_err_detail(e2)}"
                            )
                            await _add_totals(session_name, skipped=1, username_nf=1)
                            await _finalize_user(progress, task_id)
                            return "continue_outer"
                        await _log(
                            f"  [yellow]relay[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                            f"{escape(_err_tag(e2))}: {_err_detail(e2)}"
                        )
                        await _try_requeue(
                            u, tried, session_name, preview, progress, task_id
                        )
                        return "continue_outer"

                try:
                    try:
                        await _set_worker_status(session_name, "отправка…")
                        await _try_send_resilient()
                    except FloodWaitError as e_first:
                        e_fw = e_first
                        send_ok = False
                        flood_peer_continue = False
                        for _fw_round in range(50):
                            sec = await _next_broadcast_flood_wait_seconds(
                                session_name, e_fw
                            )
                            if sec <= 0:
                                await _set_worker_status(session_name, "активно")
                                await _log(
                                    f"  [yellow]FloodWait[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                                    "сервер не вернул длительность, relay"
                                )
                                break
                            pool.mark_flood_wait(session_name, sec)
                            await _set_worker_status(
                                session_name,
                                f"FloodWait {sec // 60}м ({sec}s)",
                            )
                            await _log(
                                f"  [yellow]FloodWait[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                                f"ожидание [bold]{sec}[/] с [dim](max TG/лестница, раунд {_fw_round + 1})[/]"
                            )
                            await sleep_flood_wait(
                                sec, console=console, session_label=session_name
                            )
                            await _set_worker_status(session_name, "активно")
                            try:
                                await _set_worker_status(session_name, "отправка…")
                                await _try_send_resilient()
                                send_ok = True
                                break
                            except FloodWaitError as e_again:
                                e_fw = e_again
                                continue
                            except PeerFloodError as e_pf:
                                pr = await _peer_flood_after(e_pf)
                                if pr == "exit_worker":
                                    return
                                if pr == "continue_outer":
                                    flood_peer_continue = True
                                    break
                                send_ok = True
                                break
                        if flood_peer_continue:
                            continue
                        if not send_ok:
                            await _log(
                                f"  [yellow]relay[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                                "FloodWait после ожиданий"
                            )
                            await _try_requeue(
                                u, tried, session_name, preview, progress, task_id
                            )
                            continue
                    except PeerFloodError as e:
                        pr = await _peer_flood_after(e)
                        if pr == "exit_worker":
                            return
                        if pr == "continue_outer":
                            continue
                    except UserPrivacyRestrictedError:
                        if uid is not None:
                            await db.mark_broadcast_privacy_blocked(int(uid))
                        await _log(
                            f"  [yellow]privacy[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                            "приватность; в очередь для повтора"
                        )
                        await _add_totals(session_name, privacy=1)
                        await _finalize_user(progress, task_id)
                    except (UsernameNotOccupiedError, UsernameInvalidError) as e:
                        if uid is not None:
                            await db.mark_username_not_found(int(uid))
                            await _add_totals(session_name, skipped=1, username_nf=1)
                        else:
                            await _add_totals(session_name, skipped=1)
                        await _log(
                            f"  [yellow]skip[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                            f"нет @username в Telegram [dim](в БД помечен)[/]: "
                            f"{escape(_err_tag(e))}: {_err_detail(e)}"
                        )
                        await _finalize_user(progress, task_id)
                    except (
                        UserIsBlockedError,
                        PeerIdInvalidError,
                        InputUserDeactivatedError,
                        UserIdInvalidError,
                    ) as e:
                        await _log(
                            f"  [yellow]skip[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                            f"{escape(_err_tag(e))}: {_err_detail(e)}"
                        )
                        await _add_totals(session_name, skipped=1)
                        await _finalize_user(progress, task_id)
                    except ValueError as e:
                        un_m = (
                            uid is not None and is_username_missing_telegram_error(e)
                        )
                        if un_m:
                            await db.mark_username_not_found(int(uid))
                        await _log(
                            f"  [yellow]skip[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                            f"ValueError: {_err_detail(e)}"
                            + (" [dim](в БД помечен как нет @username)[/]" if un_m else "")
                        )
                        await _add_totals(
                            session_name, skipped=1, username_nf=1 if un_m else 0
                        )
                        await _finalize_user(progress, task_id)
                    except RPCError as e:
                        if uid is not None and is_username_missing_telegram_error(e):
                            await db.mark_username_not_found(int(uid))
                            await _log(
                                f"  [yellow]skip[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                                f"нет @username (RPC), в БД помечен: {_err_detail(e)}"
                            )
                            await _add_totals(session_name, skipped=1, username_nf=1)
                            await _finalize_user(progress, task_id)
                        else:
                            await _log(
                                f"  [yellow]relay[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                                f"RPCError: {_err_detail(e)}"
                            )
                            await _try_requeue(
                                u, tried, session_name, preview, progress, task_id
                            )
                            continue
                    except Exception as e:
                        if uid is not None and is_username_missing_telegram_error(e):
                            await db.mark_username_not_found(int(uid))
                            await _log(
                                f"  [yellow]skip[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                                f"нет @username, в БД помечен: {escape(_err_tag(e))}: {_err_detail(e)}"
                            )
                            await _add_totals(session_name, skipped=1, username_nf=1)
                            await _finalize_user(progress, task_id)
                        else:
                            await _log(
                                f"  [yellow]relay[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                                f"{escape(_err_tag(e))}: {_err_detail(e)}"
                            )
                            await _try_requeue(
                                u, tried, session_name, preview, progress, task_id
                            )
                            continue
                except _DegradedExit:
                    return

                await asyncio.sleep(
                    smart_delay(settings.delay_broadcast_min, settings.delay_broadcast_max)
                )
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    mode_label = "повтор privacy" if broadcast_mode == "privacy_retry" else "обычная"
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task(
            "[cyan]Рассылка[/]",
            total=total_tasks,
        )
        precache_on = settings.broadcast_precache_media_to_saved
        media_part = (
            f"медиа: [white]да[/], precache: [white]{precache_on}[/]"
            if send_media
            else "медиа: [white]нет[/] [dim](только текст)[/]"
        )
        await _log(
            f"[bold]Старт рассылки[/] ([dim]{escape(mode_label)}[/]): аккаунтов [white]{n_acc}[/], "
            f"получателей [white]{len(users)}[/], лимит/акк/сутки UTC: [white]{daily_limit or '—'}[/], "
            f"relay max акк/получатель: [white]{max_attempts}[/], "
            f"retire после PeerFlood: [white]{retire_after}[/], "
            f"[dim]PeerFlood — пауза по той же лестнице, что FloodWait (без секунд из RPC)[/], "
            f"{media_part}, "
            f"прокси: [white]{is_proxy_enabled()}[/]"
        )
        await _log("[dim]Ниже таблица статусов аккаунтов обновляется ~1 с.[/]")

        stop_live = asyncio.Event()

        async def _live_status_loop() -> None:
            live = Live(console=console, refresh_per_second=1.2)
            live.start()
            try:
                while not stop_live.is_set():
                    async with status_lock:
                        snap = sorted(account_status.items(), key=lambda x: x[0])
                    tab = Table(
                        title="[bold cyan]Аккаунты[/] — рассылка",
                        show_lines=False,
                        header_style="bold",
                    )
                    tab.add_column("Сессия", max_width=20, overflow="ellipsis")
                    tab.add_column("Статус", max_width=44, overflow="ellipsis")
                    for sn, st in snap:
                        tab.add_row(escape(str(sn)[:18]), escape(str(st)[:42]))
                    live.update(tab)
                    try:
                        await asyncio.wait_for(stop_live.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
            finally:
                live.stop()

        live_task = asyncio.create_task(_live_status_loop())
        try:
            await asyncio.gather(
                *(
                    _worker(session_names[i], progress, task_id, worker_index=i)
                    for i in range(n_acc)
                )
            )
        finally:
            stop_live.set()
            live_task.cancel()
            with suppress(asyncio.CancelledError):
                await live_task

        drained = 0
        while True:
            try:
                left = work_q.get_nowait()
            except asyncio.QueueEmpty:
                break
            if left is _SHUTDOWN:
                continue
            assert isinstance(left, _WorkItem)
            u, tried = left.user, left.tried
            ident = u.get("username") or u.get("telegram_id") or "?"
            preview = str(ident)[:40]
            sn0 = session_names[0] if session_names else "?"
            await _relay_exhausted_log(
                sn0, u, preview, detail="прервано (drain очереди)"
            )
            await _finalize_user(progress, task_id)
            drained += 1

        if drained:
            await _log(f"[yellow]Очередь очищена после воркеров:[/] {drained} шт.")

    return totals
