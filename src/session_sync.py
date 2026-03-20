"""
Автоподхват: для каждого *.session в папке сессий — запись в accounts.json.
Рядом ожидается <имя>.json с api_id/api_hash (или берутся telethon_default_api из settings).
"""
from __future__ import annotations

import json
from pathlib import Path

from src.config import (
    Settings,
    load_accounts,
    telethon_session_dir_path,
    upsert_telethon_account,
)


def _pick_api_from_dict(d: dict) -> tuple[int | None, str | None]:
    """Вытащить api_id + api_hash из плоского или вложенного dict."""
    pairs = [
        ("api_id", "api_hash"),
        ("app_id", "app_hash"),
        ("apiId", "apiHash"),
    ]
    for ik, hk in pairs:
        if ik in d and hk in d and d[hk] is not None:
            try:
                aid = int(d[ik])
                h = str(d[hk]).strip()
                if h:
                    return aid, h
            except (TypeError, ValueError):
                continue
    for nest in ("telegram", "app", "telethon", "session", "credentials"):
        sub = d.get(nest)
        if isinstance(sub, dict):
            a, h = _pick_api_from_dict(sub)
            if a is not None and h:
                return a, h
    return None, None


def _read_sidecar_session_json(session_dir: Path, stem: str) -> tuple[int | None, str | None, str | None]:
    """
    Читает sessions/<stem>.json → (api_id, api_hash, phone).
    phone — опционально (phone, phone_number).
    """
    path = session_dir / f"{stem}.json"
    if not path.is_file():
        return None, None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None
    aid, ahash = _pick_api_from_dict(data)
    phone = data.get("phone") or data.get("phone_number")
    if phone is not None:
        phone = str(phone).strip() or None
    return aid, ahash, phone


def sync_sessions_dir_to_accounts(settings: Settings | None = None) -> tuple[int, list[str]]:
    """
    Для каждого *.session без полной записи в accounts.json — upsert.
    API: из <stem>.json рядом с файлом или из telethon_default_api.

    Возвращает (сколько добавлено/обновлено, предупреждения).
    """
    s = settings or Settings()
    session_dir = telethon_session_dir_path(s)
    if not session_dir.is_dir():
        return 0, []

    in_json = {
        a.get("session_name")
        for a in load_accounts()
        if a.get("session_name")
    }

    added = 0
    warns: list[str] = []

    for sess in sorted(session_dir.glob("*.session")):
        stem = sess.stem
        if not stem:
            continue
        if stem in in_json:
            continue

        aid, ahash, phone = _read_sidecar_session_json(session_dir, stem)
        if aid is None or not ahash:
            aid = s.default_telethon_api_id
            ahash = s.default_telethon_api_hash
        if aid is None or not ahash:
            warns.append(
                f"{stem}: нет api в {stem}.json и пустой telethon_default_api в settings"
            )
            continue

        upsert_telethon_account(stem, aid, ahash, phone=phone)
        in_json.add(stem)
        added += 1

    return added, warns
