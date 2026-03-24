"""Локальный кэш id сообщений в «Избранном» после прекэша картинок рассылки (без повторной загрузки тех же файлов)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from telethon import TelegramClient, utils
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

_cache_lock = asyncio.Lock()


def default_precache_cache_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "output" / "broadcast_precache_cache.json"


def file_fingerprint(path: Path) -> str:
    p = path.expanduser().resolve()
    if not p.is_file():
        return f"missing:{p}"
    st = p.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def fingerprints_tuple(paths: tuple[Path, Path, Path]) -> tuple[str, str, str]:
    return (
        file_fingerprint(paths[0]),
        file_fingerprint(paths[1]),
        file_fingerprint(paths[2]),
    )


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


async def try_load_cached_handles(
    client: TelegramClient,
    paths: tuple[Path, Path, Path],
    session_key: str,
    cache_path: Path,
) -> tuple[object, object, object] | None:
    """
    Если в кэше те же файлы (отпечаток) и три сообщения ещё есть в Избранном — вернуть дескрипторы.
    """
    key = session_key.strip() or "_default"
    fps = fingerprints_tuple(paths)
    fp_key = "|".join(fps)

    async with _cache_lock:
        root = _load_raw(cache_path)
        by_sess = root.get("by_session")
        if not isinstance(by_sess, dict):
            return None
        entry = by_sess.get(key)
        if not isinstance(entry, dict):
            return None
        if entry.get("fp") != fp_key:
            return None
        ids = entry.get("ids")
        if not isinstance(ids, list) or len(ids) != 3:
            return None
        try:
            msg_ids = [int(x) for x in ids]
        except (TypeError, ValueError):
            return None

    msgs = await client.get_messages("me", ids=msg_ids)
    if not isinstance(msgs, list):
        msgs = [msgs]
    by_id = {int(m.id): m for m in msgs if m is not None}
    ordered = [by_id.get(mid) for mid in msg_ids]
    if len(ordered) != 3 or any(m is None for m in ordered):
        return None

    out: list[object] = []
    for msg in ordered:
        media = msg.media
        if isinstance(media, MessageMediaPhoto):
            out.append(utils.get_input_photo(media.photo))
        elif isinstance(media, MessageMediaDocument) and media.document:
            out.append(utils.get_input_document(media.document))
        else:
            return None
    return (out[0], out[1], out[2])


async def store_cache_entry(
    session_key: str,
    paths: tuple[Path, Path, Path],
    message_ids: tuple[int, int, int],
    cache_path: Path,
) -> None:
    key = session_key.strip() or "_default"
    fp_key = "|".join(fingerprints_tuple(paths))
    async with _cache_lock:
        root = _load_raw(cache_path)
        if "by_session" not in root or not isinstance(root["by_session"], dict):
            root["by_session"] = {}
        root["by_session"][key] = {
            "fp": fp_key,
            "ids": list(message_ids),
        }
        _atomic_write(cache_path, root)
