"""Последний «отдельный» вход для сбора базы (меню 2→1→2): api + имя .session на диске."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_STATE_NAME = "last_scrape_ephemeral_login.json"


def scrape_ephemeral_state_path() -> Path:
    p = Path("output") / _STATE_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _valid_session_stem(name: str) -> bool:
    return bool(name and re.match(r"^[a-zA-Z0-9_]+$", name))


def load_scrape_ephemeral_login() -> dict | None:
    path = scrape_ephemeral_state_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    sn = str(data.get("session_name") or "").strip()
    aid = data.get("api_id")
    hsh = str(data.get("api_hash") or "").strip()
    if not _valid_session_stem(sn) or not hsh:
        return None
    try:
        api_id = int(aid)
    except (TypeError, ValueError):
        return None
    data["session_name"] = sn
    data["api_id"] = api_id
    data["api_hash"] = hsh
    return data


def save_scrape_ephemeral_login(
    *,
    session_name: str,
    api_id: int,
    api_hash: str,
    phone: str | None = None,
    proxy_url: str | None = None,
) -> None:
    if not _valid_session_stem(session_name):
        return
    path = scrape_ephemeral_state_path()
    payload = {
        "session_name": session_name,
        "api_id": int(api_id),
        "api_hash": str(api_hash).strip(),
        "phone": (phone or "").strip() or None,
        "proxy_url": (proxy_url or "").strip() or None,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
