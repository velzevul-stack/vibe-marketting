"""TelegramClient с устойчивой SQLite-сессией (busy timeout / WAL) — меньше database is locked на Windows."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions.sqlite import SQLiteSession

# Telethon по умолчанию: sqlite3.connect без timeout (~5 с). Параллельные воркеры / антивирус / второй процесс.
_SQLITE_CONNECT_TIMEOUT_SEC = 120.0
_BUSY_TIMEOUT_MS = 120_000


class ResilientSQLiteSession(SQLiteSession):
    """Тот же формат .session, что у Telethon, но с ожиданием блокировки дольше."""

    def _cursor(self):
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.filename,
                check_same_thread=False,
                timeout=_SQLITE_CONNECT_TIMEOUT_SEC,
            )
            try:
                self._conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            except Exception:
                pass
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except Exception:
                pass
        return self._conn.cursor()


def telegram_client(
    session: str | Path,
    api_id: int,
    api_hash: str,
    *,
    proxy=None,
    **kwargs,
) -> TelegramClient:
    """TelegramClient с ResilientSQLiteSession (путь к файлу сессии как у Telethon — с/без .session)."""
    sess = ResilientSQLiteSession(str(session))
    return TelegramClient(sess, api_id, api_hash, proxy=proxy, **kwargs)
