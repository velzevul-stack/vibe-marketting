"""Состояние пайплайна my.telegram.org (фазы Web + портал)."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


STATE_VERSION = 1


@dataclass
class AccountJob:
    session_name: str
    phone: str
    proxy_url: str
    status: str  # pending | web_ok | portal_pending | api_ok | failed
    web_storage_path: str = ""
    wait_until_ts: float | None = None
    last_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> AccountJob:
        return cls(
            session_name=str(d.get("session_name") or ""),
            phone=str(d.get("phone") or ""),
            proxy_url=str(d.get("proxy_url") or ""),
            status=str(d.get("status") or "pending"),
            web_storage_path=str(d.get("web_storage_path") or ""),
            wait_until_ts=(
                float(d["wait_until_ts"])
                if d.get("wait_until_ts") is not None
                else None
            ),
            last_error=(
                str(d["last_error"]) if d.get("last_error") is not None else None
            ),
        )


@dataclass
class PortalState:
    version: int = STATE_VERSION
    updated_ts: float = field(default_factory=time.time)
    phase: str = "idle"  # idle | after_web | after_wait
    accounts: list[AccountJob] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "updated_ts": self.updated_ts,
            "phase": self.phase,
            "accounts": [a.to_json() for a in self.accounts],
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> PortalState:
        accs = d.get("accounts") or []
        jobs = [AccountJob.from_json(x) for x in accs if isinstance(x, dict)]
        return cls(
            version=int(d.get("version") or STATE_VERSION),
            updated_ts=float(d.get("updated_ts") or time.time()),
            phase=str(d.get("phase") or "idle"),
            accounts=jobs,
        )


def default_state_path(project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    out = root / "output"
    out.mkdir(parents=True, exist_ok=True)
    return out / "mytelegram_portal_state.json"


def storage_dir(project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    d = root / "output" / "mytg_web_storage"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_portal_state(path: Path) -> PortalState | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return PortalState.from_json(data)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def save_portal_state(path: Path, state: PortalState) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    state.updated_ts = time.time()
    p.write_text(
        json.dumps(state.to_json(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def job_by_session(state: PortalState, session_name: str) -> AccountJob | None:
    sn = (session_name or "").strip()
    for a in state.accounts:
        if a.session_name == sn:
            return a
    return None
