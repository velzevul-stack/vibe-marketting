"""Параллельная рассылка ЛС: воркер на аккаунт + общая очередь с релокализацией при флуде/лимитах."""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Literal

from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
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
from src.config import Settings, load_accounts, is_proxy_enabled
from src.db.database import Database
from src.invite.manager import AccountPool, smart_delay

BroadcastMode = Literal["normal", "privacy_retry"]

_PEER_FLOOD_COOLDOWN_SEC = 120
_SHUTDOWN = object()


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


async def _resolve_peer(client: TelegramClient, u: dict):
    """
    Сначала по числовому telegram_id; при любой ошибке резолва — fallback на username
    (часто в БД лежит битый/чужой id, а @username валиден).
    """
    un = (u.get("username") or "").strip().lstrip("@")
    tid_s = str(u.get("telegram_id") or "").strip()

    if tid_s.isdigit():
        try:
            return await client.get_entity(int(tid_s))
        except Exception:
            if un:
                return await client.get_entity(un)
            raise
    if un:
        return await client.get_entity(un)
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
) -> BroadcastTotals:
    """
    Общая asyncio.Queue получателей; при флуде/лимите суток и т.п. задача возвращается в пул
    для других аккаунтов (лимит разных аккаунтов на пользователя — в settings).
    broadcast_mode=privacy_retry — только очередь после UserPrivacyRestricted.
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
    total_tasks = len(users)

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
    ) -> None:
        async with totals_lock:
            totals.sent += sent
            totals.failed += failed
            totals.skipped += skipped
            totals.privacy_skipped += privacy
            totals.deferred_daily_cap += dailycap
            totals.relay_exhausted += relay
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

    async def _worker(session_name: str, progress: Progress, task_id: int) -> None:
        client = pool.get_client(session_name, settings=settings)
        if not client:
            await _log(f"[red]{escape(session_name)}[/]: нет клиента (api_id/api_hash)")
            async with state_lock:
                retired.add(session_name)
            await _degraded_relay_loop(session_name, progress, task_id)
            return

        try:
            try:
                await client.connect()
            except Exception as e:
                await _log(
                    f"[red]{escape(session_name)}[/]: подключение — {escape(_err_tag(e))}: "
                    f"{escape(str(e)[:80])}"
                )
                async with state_lock:
                    retired.add(session_name)
                await _degraded_relay_loop(session_name, progress, task_id)
                return

            if not await client.is_user_authorized():
                await _log(f"[red]{escape(session_name)}[/]: сессия не авторизована")
                async with state_lock:
                    retired.add(session_name)
                await _degraded_relay_loop(session_name, progress, task_id)
                return

            cached_handles: tuple[object, object, object] | None
            if settings.broadcast_precache_media_to_saved:
                try:
                    cached_handles = await precache_campaign_images(client, images)
                    await _log(
                        f"  [dim]precache[/] [dim]{escape(session_name)}[/]: "
                        "медиа 1–3.jpg закэшировано (Избранное)"
                    )
                except Exception as e:
                    await _log(
                        f"  [yellow]precache[/] [dim]{escape(session_name)}[/]: "
                        f"{escape(_err_tag(e))}: {_err_detail(e)} — отправка с диска"
                    )
                    cached_handles = None
            else:
                cached_handles = None

            while True:
                item = await work_q.get()
                if item is _SHUTDOWN:
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
                img_path = images[h % 3]
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

                async def _try_send() -> None:
                    peer = await _resolve_peer(client, u)
                    if peer is None:
                        await _log(
                            f"  [yellow]skip[/] [dim]{escape(session_name)}[/] @{escape(preview)} — нет peer"
                        )
                        await _add_totals(session_name, skipped=1)
                        await _finalize_user(progress, task_id)
                        return
                    media = (
                        cached_handles[h % 3]
                        if cached_handles is not None
                        else img_path
                    )
                    await client.send_file(peer, media, caption=caption)
                    if uid is not None:
                        await db.mark_broadcast_sent(int(uid))
                        await db.increment_broadcast_sent_today_utc(session_name)
                    pool.mark_used(session_name)
                    await _log(
                        f"  [green]OK[/] [dim]{escape(session_name)}[/] → [white]{escape(preview)}[/]"
                    )
                    await _add_totals(session_name, sent=1)
                    await _finalize_user(progress, task_id)

                try:
                    await _try_send()
                except FloodWaitError as e:
                    pool.mark_flood_wait(session_name, e.seconds)
                    await asyncio.sleep(e.seconds)
                    try:
                        await _try_send()
                    except Exception as e2:
                        await _log(
                            f"  [yellow]relay[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                            f"FloodWait повтор: {escape(_err_tag(e2))}: {_err_detail(e2)}"
                        )
                        await _try_requeue(
                            u, tried, session_name, preview, progress, task_id
                        )
                        continue
                except PeerFloodError as e:
                    pool.mark_flood_wait(session_name, _PEER_FLOOD_COOLDOWN_SEC)
                    await _log(
                        f"  [yellow]peer_flood[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                        f"пауза {_PEER_FLOOD_COOLDOWN_SEC}s, повтор… {_err_detail(e)}"
                    )
                    await asyncio.sleep(_PEER_FLOOD_COOLDOWN_SEC)
                    try:
                        await _try_send()
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
                            must_exit = session_name in retired
                        if must_exit:
                            try:
                                await client.disconnect()
                            except Exception:
                                pass
                            return
                        continue
                    except Exception as e2:
                        await _log(
                            f"  [yellow]relay[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                            f"{escape(_err_tag(e2))}: {_err_detail(e2)}"
                        )
                        await _try_requeue(
                            u, tried, session_name, preview, progress, task_id
                        )
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
                except (
                    UserIsBlockedError,
                    PeerIdInvalidError,
                    InputUserDeactivatedError,
                    UsernameNotOccupiedError,
                    UsernameInvalidError,
                    UserIdInvalidError,
                ) as e:
                    await _log(
                        f"  [yellow]skip[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                        f"{escape(_err_tag(e))}: {_err_detail(e)}"
                    )
                    await _add_totals(session_name, skipped=1)
                    await _finalize_user(progress, task_id)
                except ValueError as e:
                    await _log(
                        f"  [yellow]skip[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                        f"ValueError: {_err_detail(e)}"
                    )
                    await _add_totals(session_name, skipped=1)
                    await _finalize_user(progress, task_id)
                except RPCError as e:
                    await _log(
                        f"  [yellow]relay[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                        f"RPCError: {_err_detail(e)}"
                    )
                    await _try_requeue(
                        u, tried, session_name, preview, progress, task_id
                    )
                    continue
                except Exception as e:
                    await _log(
                        f"  [yellow]relay[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                        f"{escape(_err_tag(e))}: {_err_detail(e)}"
                    )
                    await _try_requeue(
                        u, tried, session_name, preview, progress, task_id
                    )
                    continue

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
        await _log(
            f"[bold]Старт рассылки[/] ([dim]{escape(mode_label)}[/]): аккаунтов [white]{n_acc}[/], "
            f"получателей [white]{len(users)}[/], лимит/акк/сутки UTC: [white]{daily_limit or '—'}[/], "
            f"relay max акк/получатель: [white]{max_attempts}[/], "
            f"retire после PeerFlood: [white]{retire_after}[/], "
            f"precache медиа: [white]{precache_on}[/], "
            f"прокси: [white]{is_proxy_enabled()}[/]"
        )
        await asyncio.gather(
            *(_worker(session_names[i], progress, task_id) for i in range(n_acc))
        )

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
