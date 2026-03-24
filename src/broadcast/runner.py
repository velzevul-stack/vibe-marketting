"""Параллельная рассылка ЛС: один asyncio-воркер на аккаунт, разбиение базы по modulo."""
from __future__ import annotations

import asyncio
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
from src.broadcast.text_jitter import apply_caption_homoglyph
from src.config import Settings, load_accounts, is_proxy_enabled
from src.db.database import Database
from src.invite.manager import AccountPool, smart_delay

BroadcastMode = Literal["normal", "privacy_retry"]

# PeerFloodError не содержит seconds от сервера — консервативная пауза перед повтором
_PEER_FLOOD_COOLDOWN_SEC = 120


def _err_tag(exc: BaseException) -> str:
    return type(exc).__name__


def _err_detail(exc: BaseException, limit: int = 120) -> str:
    s = (str(exc) or "").strip() or repr(exc)
    return escape(s[:limit])


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


@dataclass
class BroadcastTotals:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    privacy_skipped: int = 0
    deferred_daily_cap: int = 0
    by_session: dict[str, tuple[int, int, int, int, int]] = field(default_factory=dict)
    # sent, failed, skipped, privacy, daily_cap


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
    Рассылка пользователям из БД: тексты чередуются по индексу внутри воркера,
    картинки по циклу 1.jpg → 3.jpg.
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

    n_acc = len(session_names)
    chunks: list[list[dict]] = [[] for _ in range(n_acc)]
    for i, u in enumerate(users):
        chunks[i % n_acc].append(u)

    totals = BroadcastTotals()
    for sn in session_names:
        totals.by_session[sn] = (0, 0, 0, 0, 0)

    totals_lock = asyncio.Lock()
    log_lock = asyncio.Lock()
    prog_lock = asyncio.Lock()
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
    ) -> None:
        async with totals_lock:
            totals.sent += sent
            totals.failed += failed
            totals.skipped += skipped
            totals.privacy_skipped += privacy
            totals.deferred_daily_cap += dailycap
            s, f, sk, pr, dc = totals.by_session[sn]
            totals.by_session[sn] = (
                s + sent,
                f + failed,
                sk + skipped,
                pr + privacy,
                dc + dailycap,
            )

    async def _worker(
        session_name: str,
        chunk: list[dict],
        progress: Progress,
        task_id: int,
    ) -> None:
        if not chunk:
            return
        client = pool.get_client(session_name, settings=settings)
        if not client:
            await _log(f"[red]{escape(session_name)}[/]: нет клиента (api_id/api_hash)")
            for _ in chunk:
                await _add_totals(session_name, failed=1)
                await _bump_task(progress, task_id)
            return

        try:
            try:
                await client.connect()
            except Exception as e:
                await _log(
                    f"[red]{escape(session_name)}[/]: подключение — {escape(_err_tag(e))}: "
                    f"{escape(str(e)[:80])}"
                )
                for _ in chunk:
                    await _add_totals(session_name, failed=1)
                    await _bump_task(progress, task_id)
                return

            if not await client.is_user_authorized():
                await _log(f"[red]{escape(session_name)}[/]: сессия не авторизована")
                for _ in chunk:
                    await _add_totals(session_name, failed=1)
                    await _bump_task(progress, task_id)
                return

            for local_i, u in enumerate(chunk):
                uid = u.get("id")
                raw_caption = texts[local_i % 2]
                caption = apply_caption_homoglyph(raw_caption, settings)
                img_path = images[local_i % 3]
                ident = (u.get("username") or u.get("telegram_id") or "?")
                preview = str(ident)[:40]

                if daily_limit > 0:
                    n_today = await db.get_broadcast_sent_today_utc(session_name)
                    if n_today >= daily_limit:
                        await _log(
                            f"  [yellow]daily_cap[/] [dim]{escape(session_name)}[/] "
                            f"{escape(preview)} — лимит {daily_limit}/сутки UTC"
                        )
                        await _add_totals(session_name, dailycap=1)
                        await _bump_task(progress, task_id)
                        if local_i + 1 < len(chunk):
                            await asyncio.sleep(
                                smart_delay(
                                    settings.delay_broadcast_min,
                                    settings.delay_broadcast_max,
                                )
                            )
                        continue

                async def _try_send() -> None:
                    peer = await _resolve_peer(client, u)
                    if peer is None:
                        await _log(
                            f"  [yellow]skip[/] [dim]{escape(session_name)}[/] @{escape(preview)} — нет peer"
                        )
                        await _add_totals(session_name, skipped=1)
                        return
                    await client.send_file(peer, img_path, caption=caption)
                    if uid is not None:
                        await db.mark_broadcast_sent(int(uid))
                        await db.increment_broadcast_sent_today_utc(session_name)
                    pool.mark_used(session_name)
                    await _log(
                        f"  [green]OK[/] [dim]{escape(session_name)}[/] → [white]{escape(preview)}[/]"
                    )
                    await _add_totals(session_name, sent=1)

                try:
                    await _try_send()
                except FloodWaitError as e:
                    pool.mark_flood_wait(session_name, e.seconds)
                    await asyncio.sleep(e.seconds)
                    try:
                        await _try_send()
                    except Exception as e2:
                        await _log(
                            f"  [red]FAIL[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                            f"{escape(_err_tag(e2))}: {_err_detail(e2)}"
                        )
                        await _add_totals(session_name, failed=1)
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
                            f"  [red]FAIL[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                            f"PeerFloodError (повтор): {_err_detail(e2)}"
                        )
                        await _add_totals(session_name, failed=1)
                    except Exception as e2:
                        await _log(
                            f"  [red]FAIL[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                            f"{escape(_err_tag(e2))}: {_err_detail(e2)}"
                        )
                        await _add_totals(session_name, failed=1)
                except UserPrivacyRestrictedError:
                    if uid is not None:
                        await db.mark_broadcast_privacy_blocked(int(uid))
                    await _log(
                        f"  [yellow]privacy[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                        "приватность; в очередь для повтора"
                    )
                    await _add_totals(session_name, privacy=1)
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
                except ValueError as e:
                    await _log(
                        f"  [yellow]skip[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                        f"ValueError: {_err_detail(e)}"
                    )
                    await _add_totals(session_name, skipped=1)
                except RPCError as e:
                    await _log(
                        f"  [red]FAIL[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                        f"{escape(_err_tag(e))}: {_err_detail(e)}"
                    )
                    await _add_totals(session_name, failed=1)
                except Exception as e:
                    await _log(
                        f"  [red]FAIL[/] [dim]{escape(session_name)}[/] {escape(preview)} — "
                        f"{escape(_err_tag(e))}: {_err_detail(e)}"
                    )
                    await _add_totals(session_name, failed=1)

                await _bump_task(progress, task_id)

                if local_i + 1 < len(chunk):
                    delay = smart_delay(settings.delay_broadcast_min, settings.delay_broadcast_max)
                    await asyncio.sleep(delay)
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
        await _log(
            f"[bold]Старт рассылки[/] ([dim]{escape(mode_label)}[/]): аккаунтов [white]{n_acc}[/], "
            f"получателей [white]{len(users)}[/], лимит/акк/сутки UTC: [white]{daily_limit or '—'}[/], "
            f"прокси: [white]{is_proxy_enabled()}[/]"
        )
        await asyncio.gather(
            *(
                _worker(session_names[i], chunks[i], progress, task_id)
                for i in range(n_acc)
            )
        )

    return totals
