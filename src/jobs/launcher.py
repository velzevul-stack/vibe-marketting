"""Запуск worker-процесса для фоновой задачи."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from src.jobs.registry import LOG_DIR, new_job_id, register_job_start


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def spawn_worker_job(payload: dict, *, task_label: str, summary: str = "") -> tuple[str, Path, Path]:
    """
    Пишет job.json, открывает лог, запускает ``python main.py --worker-job <path>``.
    Возвращает (job_id, job_json_path, log_path).
    """
    job_id = new_job_id()
    jobs_dir = Path("output") / "job_payloads"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_path = jobs_dir / f"{job_id}.json"
    job_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{job_id}.log"
    main_py = project_root() / "main.py"
    log_f = open(log_path, "w", encoding="utf-8")
    cmd = [
        sys.executable,
        str(main_py.resolve()),
        "--worker-job",
        str(job_path.resolve()),
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        cmd,
        cwd=str(project_root()),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=os.name != "nt",
    )
    log_f.close()
    register_job_start(
        job_id=job_id,
        pid=proc.pid or 0,
        task=task_label,
        log_path=log_path,
        payload_summary=summary,
    )
    return job_id, job_path, log_path
