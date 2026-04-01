"""Выполнение задачи из JSON (дочерний процесс ``--worker-job``)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rich.console import Console


def run_worker_job_file(path: Path) -> int:
    con = Console()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        con.print(f"[red]job file: {e}[/]")
        return 1
    ver = data.get("version", 1)
    if ver != 1:
        con.print(f"[red]unsupported job version: {ver}[/]")
        return 1
    task = data.get("task")
    payload = data.get("payload") or {}
    if task == "bulk_prepare":
        return _run_bulk_prepare(con, payload)
    if task == "mytg":
        return _run_mytg(con, payload)
    if task == "broadcast_bundle":
        return _run_broadcast_bundle(payload)
    con.print(f"[red]unknown task: {task!r}[/]")
    return 1


def _run_bulk_prepare(con: Console, payload: dict) -> int:
    from src.accounts_bulk_prepare import run_bulk_account_prepare

    names = payload.get("only_session_names")
    only = frozenset(str(x).strip() for x in names if x and str(x).strip()) if names else None
    pwd = payload.get("password_plain")
    pwd_s = str(pwd) if pwd is not None else None

    async def _go() -> None:
        await run_bulk_account_prepare(
            con,
            only_session_names=only,
            password_plain=pwd_s,
        )

    asyncio.run(_go())
    return 0


def _run_mytg(con: Console, payload: dict) -> int:
    from src.mytelegram_portal.runner import run_mytg_menu_flow
    from src.mytelegram_portal.state import AccountJob

    mode = payload.get("mode", "full")
    from_sess = bool(payload.get("from_session_files", False))
    jobs = payload.get("jobs_override")
    jobs_override = None
    if isinstance(jobs, list) and jobs:
        jobs_override = []
        for item in jobs:
            if isinstance(item, dict):
                jobs_override.append(AccountJob.from_json(item))
    return run_mytg_menu_flow(
        con,
        mode=mode,  # type: ignore[arg-type]
        from_session_files=from_sess,
        jobs_override=jobs_override,
    )


def _run_broadcast_bundle(payload: dict) -> int:
    import main as vibe_main

    root = str(payload.get("campaign_dir", "")).strip()
    if not root:
        return 1
    limit = int(payload.get("limit", 200))
    category = str(payload.get("category", "hot"))
    zip_conflict = str(payload.get("zip_conflict", "skip"))
    broadcast_mode = str(payload.get("broadcast_mode", "normal"))
    send_media = bool(payload.get("send_media", True))
    exclude_invited = bool(payload.get("exclude_invited", True))
    raw_names = payload.get("only_session_names")
    only = None
    if raw_names:
        only = frozenset(str(x).strip() for x in raw_names if x and str(x).strip())
    return vibe_main._cli_broadcast(
        root,
        limit,
        category,
        zip_conflict,
        broadcast_mode,
        send_media=send_media,
        broadcast_delay_spec=payload.get("broadcast_delay_minutes"),
        broadcast_account_gap_spec=payload.get("broadcast_account_gap_minutes"),
        only_session_names=only,
        exclude_invited=exclude_invited,
    )
