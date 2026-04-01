"""Реестр фоновых задач (append-only JSONL)."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path("output") / "job_registry.jsonl"
LOG_DIR = Path("output") / "job_logs"


def _ensure_dirs() -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def append_registry_record(record: dict[str, Any]) -> None:
    _ensure_dirs()
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(REGISTRY_PATH, "a", encoding="utf-8") as f:
        f.write(line)


def new_job_id() -> str:
    return f"{int(time.time())}_{uuid.uuid4().hex[:8]}"


def register_job_start(
    *,
    job_id: str,
    pid: int,
    task: str,
    log_path: Path,
    payload_summary: str = "",
) -> None:
    append_registry_record(
        {
            "job_id": job_id,
            "pid": pid,
            "task": task,
            "log_path": str(log_path.resolve()),
            "status": "running",
            "started_at": time.time(),
            "summary": payload_summary[:500],
        }
    )


def register_job_end(*, job_id: str, status: str, exit_code: int | None = None) -> None:
    append_registry_record(
        {
            "job_id": job_id,
            "status": status,
            "ended_at": time.time(),
            "exit_code": exit_code,
        }
    )


def read_recent_registry_entries(*, limit: int = 40) -> list[dict[str, Any]]:
    if not REGISTRY_PATH.is_file():
        return []
    lines = REGISTRY_PATH.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for ln in lines[-200:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    # последние записи по времени — оставить последние limit «событий» с job_id
    by_job: dict[str, dict[str, Any]] = {}
    for rec in out:
        jid = rec.get("job_id")
        if isinstance(jid, str):
            prev = by_job.get(jid) or {}
            merged = {**prev, **rec}
            by_job[jid] = merged
    merged_list = list(by_job.values())
    merged_list.sort(key=lambda r: float(r.get("started_at") or r.get("ended_at") or 0), reverse=True)
    return merged_list[:limit]


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False
