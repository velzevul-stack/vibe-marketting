"""Рассылка ЛС по CSV из пакета campaign: без БД, без relay, фиксированная пауза между отправками."""
from __future__ import annotations

import asyncio
import csv
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

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
from telethon.errors import (
    FloodWaitError,
    InputUserDeactivatedError,
    PeerFloodError,
    PeerIdInvalidError,
    RPCError,
    UserIdInvalidError,
    UserIsBlockedError,
    UserPrivacyRestrictedError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from src.broadcast.bundle import CampaignBundle, read_campaign_texts
from src.broadcast.send_pacing import CsvSendPacer
from src.broadcast.media_precache import precache_campaign_images
from src.broadcast.text_jitter import apply_caption_homoglyph
from src.broadcast.runner import (
    BroadcastTotals,
    _broadcast_flood_floor_for_index,
    _connect_telethon_sqlite_resilient,
    _err_detail,
    _err_tag,
    _is_telethon_disconnected_error,
    _resolve_peer,
    _user_stable_hash,
)
from src.config import Settings, is_proxy_enabled, telethon_session_file
from src.invite.manager import AccountPool
from src.telethon_flood_wait import flood_wait_seconds, sleep_flood_wait
from src.telethon_username_resolve import is_username_missing_telegram_error

_DISCONNECT_RECONNECT_ATTEMPTS = 3
_MAX_PEER_FLOOD_ROUNDS_SAME_USER = 8
_MAX_FLOOD_WAIT_ROUNDS = 50


def _norm_header(s: str) -> str:
    return "".join((s or "").strip().lower().split())


def load_sent_user_ids_from_jsonl(path: Path) -> frozenset[str]:
    if not path.is_file():
        return frozenset()
    out: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
            uid = o.get("user_id")
            if uid is not None and str(uid).strip():
                out.add(str(uid).strip())
        except json.JSONDecodeError:
            continue
    return frozenset(out)


def append_sent_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _row_get(row: dict[str, str], *candidates: str) -> str:
    norm = {_norm_header(k): (v or "").strip() for k, v in row.items()}
    for c in candidates:
        v = norm.get(_norm_header(c))
        if v:
            return v
    return ""


@dataclass
class CsvLoadResult:
    users: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_csv_recipients(
    path: Path,
    *,
    limit: int | None,
    skip_user_ids: frozenset[str],
) -> CsvLoadResult:
    """
    CSV с колонками вроде ``User ID``, ``Username`` (регистр и пробелы в заголовке не важны).
    Возвращает ``users`` как dict с ключами ``telegram_id``, ``username`` (для ``_resolve_peer``).
    """
    out = CsvLoadResult()
    p = Path(path)
    if not p.is_file():
        out.warnings.append(f"Нет файла: {p}")
        return out
    seen: set[str] = set()
    try:
        with p.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                out.warnings.append("CSV: пустой заголовок")
                return out
            for i, row in enumerate(reader, start=2):
                if limit is not None and len(out.users) >= limit:
                    break
                uid = _row_get(row, "User ID", "user_id", "userid", "telegram_id", "id")
                username = _row_get(row, "Username", "username", "user name")
                if not uid and not username:
                    continue
                if uid and not uid.isdigit():
                    out.warnings.append(f"строка {i}: User ID не число, пропуск")
                    continue
                dedupe = uid if uid else f"u:{username.lower()}"
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                if uid and uid in skip_user_ids:
                    continue
                out.users.append(
                    {
                        "telegram_id": uid,
                        "username": username.lstrip("@") if username else "",
                    }
                )
    except OSError as e:
        out.warnings.append(f"CSV: {e}")
    return out


def split_recipients_across_sessions(
    recipients: list[dict],
    session_names: list[str],
) -> dict[str, list[dict]]:
    names = [s for s in session_names if s]
    if not names:
        return {}
    buckets: dict[str, list[dict]] = {n: [] for n in names}
    for i, u in enumerate(recipients):
        buckets[names[i % len(names)]].append(u)
    return buckets


def _session_connect_lock(
    session_name: str,
    settings: Settings,
    locks: dict[str, asyncio.Lock],
) -> asyncio.Lock:
    key = str(telethon_session_file(session_name, settings).resolve())
    if key not in locks:
        locks[key] = asyncio.Lock()
    return locks[key]


async def run_csv_dm_broadcast(
    *,
    bundle: CampaignBundle,
    settings: Settings,
    console: Console,
    recipients: list[dict],
    session_names: list[str],
    delay_seconds: float,
    send_media: bool,
    sent_log_path: Path | None,
    account_gap_seconds: float = 0.0,
) -> BroadcastTotals:
    """
    Параллельные воркеры (один на сессию), очередь получателей только у своей сессии.
    ``delay_seconds`` — минимум между отправками одной сессии (и до первой после precache).
    ``account_gap_seconds`` — дополнительно не меньше этого интервала между любыми двумя успешными
    отправками; стемы со сдвигом ``stagger_index * gap`` по первому окну, чтобы не слать пачкой.
    """
    texts = read_campaign_texts(bundle)
    images = bundle.image_paths
    names = sorted({s.strip() for s in session_names if s and str(s).strip()})
    if not names:
        console.print("[red]csv рассылка: нет сессий[/]")
        return BroadcastTotals()
    if not recipients:
        console.print("[yellow]csv рассылка: нет получателей[/]")
        return BroadcastTotals()

    buckets = split_recipients_across_sessions(recipients, names)
    total_tasks = sum(len(buckets.get(s, [])) for s in names)
    totals = BroadcastTotals()
    for sn in names:
        totals.by_session[sn] = (0, 0, 0, 0, 0, 0)

    totals_lock = asyncio.Lock()
    log_lock = asyncio.Lock()
    session_connect_locks: dict[str, asyncio.Lock] = {}
    pacer = CsvSendPacer(
        own_interval_sec=max(0.0, float(delay_seconds)),
        account_gap_sec=max(0.0, float(account_gap_seconds)),
    )

    async def _log(msg: str) -> None:
        async with log_lock:
            console.print(msg)

    async def _add_totals(
        sn: str,
        *,
        sent: int = 0,
        failed: int = 0,
        skipped: int = 0,
    ) -> None:
        async with totals_lock:
            totals.sent += sent
            totals.failed += failed
            totals.skipped += skipped
            s, f, sk, pr, dc, rx = totals.by_session[sn]
            totals.by_session[sn] = (s + sent, f + failed, sk + skipped, pr, dc, rx)

    async def _worker(session_name: str, worker_index: int) -> None:
        pool = AccountPool()
        pool.ensure_account_state(session_name)
        client = pool.get_client(session_name, settings=settings)
        if not client:
            await _log(
                f"[red]{escape(session_name)}[/]: нет клиента (api_id/api_hash в accounts.json)"
            )
            n = len(buckets.get(session_name, []))
            if n:
                await _add_totals(session_name, failed=n)
            return

        try:
            await asyncio.sleep(worker_index * 0.12 + random.uniform(0, 0.08))
            try:
                async with _session_connect_lock(session_name, settings, session_connect_locks):
                    await _connect_telethon_sqlite_resilient(client)
            except Exception as e:
                await _log(
                    f"[red]{escape(session_name)}[/]: подключение — {_err_tag(e)}: "
                    f"{escape(str(e)[:100])}"
                )
                n = len(buckets.get(session_name, []))
                if n:
                    await _add_totals(session_name, failed=n)
                return

            if not await client.is_user_authorized():
                await _log(f"[red]{escape(session_name)}[/]: сессия не авторизована")
                n = len(buckets.get(session_name, []))
                if n:
                    await _add_totals(session_name, failed=n)
                return

            cached_handles: tuple[object, object, object] | None = None
            if send_media and settings.broadcast_precache_media_to_saved:
                try:
                    cached_handles, _disk = await precache_campaign_images(
                        client,
                        images,
                        console=console,
                        session_label=session_name,
                    )
                except Exception as e:
                    await _log(
                        f"  [yellow]precache[/] {escape(session_name)}: {_err_tag(e)} — с диска"
                    )
                    cached_handles = None

            pacer.mark_session_ready_after_precache(
                session_name, stagger_index=worker_index
            )

            async def _reconnect_client(reason: str) -> bool:
                for attempt in range(1, _DISCONNECT_RECONNECT_ATTEMPTS + 1):
                    await _log(
                        f"  [yellow]reconnect[/] {escape(session_name)} "
                        f"{attempt}/{_DISCONNECT_RECONNECT_ATTEMPTS} {escape(reason[:72])}"
                    )
                    try:
                        async with _session_connect_lock(session_name, settings, session_connect_locks):
                            try:
                                await client.disconnect()
                            except Exception:
                                pass
                            await _connect_telethon_sqlite_resilient(client)
                        if await client.is_user_authorized():
                            return True
                    except Exception as ex:
                        await _log(
                            f"  [dim]reconnect fail[/] {escape(session_name)}: "
                            f"{_err_tag(ex)}: {escape(str(ex)[:80])}"
                        )
                    await asyncio.sleep(0.4 * attempt)
                return False

            for u in buckets.get(session_name, []):
                await pacer.wait_before_send(session_name)
                h = _user_stable_hash(u)
                raw_caption = texts[h % 2]
                caption = apply_caption_homoglyph(raw_caption, settings)
                img_path = images[h % 3] if send_media else None
                ident = u.get("username") or u.get("telegram_id") or "?"
                preview = str(ident)[:40]
                flood_round = 0
                peer_round = 0

                async def _body_send() -> bool:
                    """True если сообщение ушло; False если нет peer (без исключения)."""
                    peer = await _resolve_peer(client, u)
                    if peer is None:
                        return False
                    if send_media:
                        media = (
                            cached_handles[h % 3]
                            if cached_handles is not None
                            else img_path
                        )
                        await client.send_file(peer, media, caption=caption)
                    else:
                        await client.send_message(peer, caption)
                    return True

                async def _send_with_reconnect() -> str:
                    """Возвращает ``sent`` | ``skipped`` | ``fail_conn``; бросает FloodWait/PeerFlood и др."""
                    while True:
                        try:
                            if not await _body_send():
                                await _log(
                                    f"  [yellow]skip[/] {escape(session_name)} {escape(preview)} — нет peer"
                                )
                                await _add_totals(session_name, skipped=1)
                                return "skipped"
                            pool.mark_used(session_name)
                            await _log(f"  [green]OK[/] {escape(session_name)} → {escape(preview)}")
                            await _add_totals(session_name, sent=1)
                            if sent_log_path is not None:
                                append_sent_jsonl(
                                    sent_log_path,
                                    {
                                        "user_id": str(u.get("telegram_id") or ""),
                                        "username": str(u.get("username") or ""),
                                        "session_name": session_name,
                                        "ts": datetime.now(timezone.utc).isoformat(),
                                    },
                                )
                            return "sent"
                        except (ConnectionError, OSError) as e:
                            if not _is_telethon_disconnected_error(e):
                                raise
                            if not await _reconnect_client(str(e)[:120]):
                                await _log(
                                    f"  [red]fail[/] {escape(session_name)} {escape(preview)} — "
                                    "не удалось переподключиться"
                                )
                                await _add_totals(session_name, failed=1)
                                return "fail_conn"

                user_finished = False
                while not user_finished:
                    try:
                        outcome = await _send_with_reconnect()
                        user_finished = True
                        if outcome == "sent":
                            await pacer.mark_sent(session_name)
                    except FloodWaitError as e_fw:
                        send_ok = False
                        for _ in range(_MAX_FLOOD_WAIT_ROUNDS):
                            sec = max(
                                flood_wait_seconds(e_fw),
                                _broadcast_flood_floor_for_index(flood_round),
                            )
                            flood_round += 1
                            if sec <= 0:
                                await _log(
                                    f"  [yellow]FloodWait[/] {escape(session_name)} {escape(preview)} — "
                                    "нет секунд, пропуск"
                                )
                                await _add_totals(session_name, skipped=1)
                                user_finished = True
                                break
                            pool.mark_flood_wait(session_name, sec)
                            await _log(
                                f"  [yellow]FloodWait[/] {escape(session_name)} {escape(preview)} — "
                                f"ждём {sec}s"
                            )
                            await sleep_flood_wait(
                                sec, console=console, session_label=session_name
                            )
                            try:
                                o2 = await _send_with_reconnect()
                                if o2 in ("sent", "skipped", "fail_conn"):
                                    user_finished = True
                                    send_ok = o2 == "sent"
                                    if o2 == "sent":
                                        await pacer.mark_sent(session_name)
                                break
                            except FloodWaitError as e2:
                                e_fw = e2
                                continue
                        if user_finished:
                            break
                        await _log(
                            f"  [yellow]skip[/] {escape(session_name)} {escape(preview)} — "
                            "FloodWait исчерпан"
                        )
                        await _add_totals(session_name, skipped=1)
                        user_finished = True
                    except PeerFloodError as e_pf:
                        peer_round += 1
                        if peer_round > _MAX_PEER_FLOOD_ROUNDS_SAME_USER:
                            await _log(
                                f"  [yellow]skip[/] {escape(session_name)} {escape(preview)} — "
                                f"PeerFlood ×{_MAX_PEER_FLOOD_ROUNDS_SAME_USER}"
                            )
                            await _add_totals(session_name, skipped=1)
                            user_finished = True
                            break
                        sec = _broadcast_flood_floor_for_index(peer_round - 1)
                        pool.mark_flood_wait(session_name, sec)
                        await _log(
                            f"  [yellow]PeerFlood[/] {escape(session_name)} {escape(preview)} — "
                            f"ждём {sec}s [dim]({_err_detail(e_pf)})[/]"
                        )
                        await sleep_flood_wait(
                            sec,
                            console=console,
                            session_label=session_name,
                            prefix="PeerFlood",
                        )
                    except UserPrivacyRestrictedError:
                        await _log(
                            f"  [yellow]privacy[/] {escape(session_name)} {escape(preview)}"
                        )
                        await _add_totals(session_name, skipped=1)
                        user_finished = True
                    except (UsernameNotOccupiedError, UsernameInvalidError) as e:
                        await _log(
                            f"  [yellow]skip[/] {escape(session_name)} {escape(preview)} — "
                            f"{_err_tag(e)}: {_err_detail(e)}"
                        )
                        await _add_totals(session_name, skipped=1)
                        user_finished = True
                    except (
                        UserIsBlockedError,
                        PeerIdInvalidError,
                        InputUserDeactivatedError,
                        UserIdInvalidError,
                    ) as e:
                        await _log(
                            f"  [yellow]skip[/] {escape(session_name)} {escape(preview)} — "
                            f"{_err_tag(e)}: {_err_detail(e)}"
                        )
                        await _add_totals(session_name, skipped=1)
                        user_finished = True
                    except RPCError as e:
                        if is_username_missing_telegram_error(e):
                            await _log(
                                f"  [yellow]skip[/] {escape(session_name)} {escape(preview)} — "
                                f"{_err_detail(e)}"
                            )
                            await _add_totals(session_name, skipped=1)
                        else:
                            await _log(
                                f"  [yellow]fail[/] {escape(session_name)} {escape(preview)} — "
                                f"RPC: {_err_detail(e)}"
                            )
                            await _add_totals(session_name, failed=1)
                        user_finished = True
                    except Exception as e:
                        if is_username_missing_telegram_error(e):
                            await _log(
                                f"  [yellow]skip[/] {escape(session_name)} {escape(preview)} — "
                                f"{_err_detail(e)}"
                            )
                            await _add_totals(session_name, skipped=1)
                        else:
                            await _log(
                                f"  [red]fail[/] {escape(session_name)} {escape(preview)} — "
                                f"{_err_tag(e)}: {_err_detail(e)}"
                            )
                            await _add_totals(session_name, failed=1)
                        user_finished = True

        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    gap_note = (
        f", межаккаунтно ≥[white]{account_gap_seconds:.0f}[/]s"
        if account_gap_seconds > 0
        else ""
    )
    await _log(
        f"[bold]CSV рассылка[/]: сессий [white]{len(names)}[/], получателей [white]{len(recipients)}[/], "
        f"задач [white]{total_tasks}[/], пауза/сессия [white]{delay_seconds:.0f}[/]s{gap_note}, "
        f"медиа: [white]{'да' if send_media else 'нет'}[/], прокси: [white]{is_proxy_enabled()}[/]"
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        tid = progress.add_task("[cyan]CSV рассылка[/]", total=total_tasks)
        prog_lock = asyncio.Lock()

        async def _wrapped(sn: str, idx: int) -> None:
            await _worker(sn, idx)
            n = len(buckets.get(sn, []))
            async with prog_lock:
                progress.advance(tid, n)

        await asyncio.gather(*(_wrapped(names[i], i) for i in range(len(names))))

    return totals
