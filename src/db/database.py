"""SQLite база данных."""
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


class Database:
    """Работа с БД."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or Path(__file__).parent.parent.parent / "output" / "vibe_marketing.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def _connect(self):
        """Подключение с ожиданием при блокировке (много параллельных воркеров рассылки)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout=20000")
            yield db

    async def init(self) -> None:
        """Создать таблицы."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=20000")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id TEXT UNIQUE,
                    title TEXT,
                    link TEXT,
                    members_count INTEGER,
                    source TEXT,
                    joined_at TEXT,
                    last_scanned_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id TEXT,
                    username TEXT,
                    category TEXT,
                    source_chat_id TEXT,
                    source_message_id INTEGER,
                    first_seen_at TEXT,
                    added_to_contacts_at TEXT,
                    invited_to_channel_at TEXT,
                    broadcast_at TEXT,
                    broadcast_privacy_blocked_at TEXT,
                    metadata TEXT,
                    UNIQUE(telegram_id, username)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_category ON users(category)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS account_broadcast_daily (
                    session_name TEXT NOT NULL,
                    day_utc TEXT NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (session_name, day_utc)
                )
                """
            )
            await db.commit()
            await self._migrate_users_broadcast_at(db)
            await self._migrate_users_broadcast_privacy(db)
            await self._migrate_users_username_not_found(db)

    async def _migrate_users_broadcast_at(self, db) -> None:
        """Добавить колонку broadcast_at в существующих БД."""
        cur = await db.execute("PRAGMA table_info(users)")
        rows = await cur.fetchall()
        cols = {r[1] for r in rows}
        if "broadcast_at" not in cols:
            await db.execute("ALTER TABLE users ADD COLUMN broadcast_at TEXT")
            await db.commit()

    async def _migrate_users_broadcast_privacy(self, db) -> None:
        """Добавить колонку broadcast_privacy_blocked_at."""
        cur = await db.execute("PRAGMA table_info(users)")
        rows = await cur.fetchall()
        cols = {r[1] for r in rows}
        if "broadcast_privacy_blocked_at" not in cols:
            await db.execute("ALTER TABLE users ADD COLUMN broadcast_privacy_blocked_at TEXT")
            await db.commit()

    async def _migrate_users_username_not_found(self, db) -> None:
        """Пометка: в Telegram нет такого @username (No user has…)."""
        cur = await db.execute("PRAGMA table_info(users)")
        rows = await cur.fetchall()
        cols = {r[1] for r in rows}
        if "username_not_found_at" not in cols:
            await db.execute("ALTER TABLE users ADD COLUMN username_not_found_at TEXT")
            await db.commit()

    async def add_chat(self, telegram_id: str, title: str, link: str, members_count: int = 0, source: str = "manual") -> None:
        """Добавить чат."""
        async with self._connect() as db:
            await db.execute(
                """INSERT OR IGNORE INTO chats (telegram_id, title, link, members_count, source, joined_at, last_scanned_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (telegram_id, title, link, members_count, source, datetime.now().isoformat(), datetime.now().isoformat()),
            )
            await db.commit()

    async def add_user(
        self,
        telegram_id: str | None,
        username: str | None,
        category: str,
        source_chat_id: str,
        source_message_id: int,
        metadata: dict | None = None,
    ) -> bool:
        """Добавить пользователя. Возвращает True если добавлен (не дубль)."""
        key = telegram_id or username or ""
        if not key:
            return False
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        async with self._connect() as db:
            try:
                await db.execute(
                    """INSERT INTO users (telegram_id, username, category, source_chat_id, source_message_id, first_seen_at, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (telegram_id, username, category, source_chat_id, source_message_id, datetime.now().isoformat(), meta_str),
                )
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def user_exists(self, telegram_id: str | None, username: str | None) -> bool:
        """Проверить наличие пользователя."""
        async with self._connect() as db:
            if telegram_id:
                cursor = await db.execute("SELECT 1 FROM users WHERE telegram_id = ?", (telegram_id,))
            elif username:
                cursor = await db.execute("SELECT 1 FROM users WHERE username = ?", (username,))
            else:
                return False
            row = await cursor.fetchone()
            return row is not None

    async def get_users(
        self,
        category: str | None = None,
        limit: int = 1000,
        exclude_invited: bool = True,
        exclude_added_to_contacts: bool = False,
        exclude_broadcast: bool = False,
        exclude_privacy_blocked: bool = True,
        only_privacy_retry: bool = False,
        exclude_username_not_found: bool = False,
    ) -> list[dict]:
        """
        Получить пользователей.
        exclude_broadcast — без успешной рассылки (broadcast_at).
        exclude_privacy_blocked — для обычной рассылки: без очереди privacy (уже отмеченных).
        only_privacy_retry — только очередь повтора (privacy без успешного broadcast_at).
        exclude_username_not_found — без строк с username_not_found_at (нет @ в Telegram).
        """
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            conds = ["1=1"]
            params = []
            if exclude_invited:
                conds.append("(invited_to_channel_at IS NULL OR invited_to_channel_at = '')")
            if exclude_added_to_contacts:
                conds.append("(added_to_contacts_at IS NULL OR added_to_contacts_at = '')")
            if exclude_broadcast:
                conds.append("(broadcast_at IS NULL OR broadcast_at = '')")
            if only_privacy_retry:
                conds.append(
                    "(broadcast_privacy_blocked_at IS NOT NULL AND broadcast_privacy_blocked_at != '')"
                )
            elif exclude_privacy_blocked:
                conds.append(
                    "(broadcast_privacy_blocked_at IS NULL OR broadcast_privacy_blocked_at = '')"
                )
            if exclude_username_not_found:
                conds.append(
                    "(username_not_found_at IS NULL OR username_not_found_at = '')"
                )
            if category:
                conds.append("category = ?")
                params.append(category)
            params.append(limit)
            sql = f"SELECT * FROM users WHERE {' AND '.join(conds)} ORDER BY id LIMIT ?"
            cursor = await db.execute(sql, tuple(params))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def mark_added_to_contacts(self, user_id: int) -> None:
        """Отметить добавление в контакты."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE users SET added_to_contacts_at = ? WHERE id = ?",
                (datetime.now().isoformat(), user_id),
            )
            await db.commit()

    async def mark_invited(self, user_id: int) -> None:
        """Отметить приглашение в канал."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE users SET invited_to_channel_at = ? WHERE id = ?",
                (datetime.now().isoformat(), user_id),
            )
            await db.commit()

    async def mark_broadcast_sent(self, user_id: int) -> None:
        """Отметить успешную рассылку ЛС; снять отметку privacy-очереди."""
        async with self._connect() as db:
            await db.execute(
                """UPDATE users SET broadcast_at = ?, broadcast_privacy_blocked_at = NULL
                   WHERE id = ?""",
                (datetime.now().isoformat(), user_id),
            )
            await db.commit()

    async def mark_broadcast_privacy_blocked(self, user_id: int) -> None:
        """Отметить UserPrivacyRestricted (без broadcast_at — для повторного прогона)."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE users SET broadcast_privacy_blocked_at = ? WHERE id = ?",
                (datetime.now().isoformat(), user_id),
            )
            await db.commit()

    async def mark_username_not_found(self, user_id: int) -> None:
        """Telegram: нет пользователя с таким @username (строка остаётся в БД)."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE users SET username_not_found_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), user_id),
            )
            await db.commit()

    async def unique_usernames_for_export(
        self,
        *,
        category: str | None = None,
        exclude_username_not_found: bool = True,
    ) -> list[str]:
        """
        Уникальные непустые username: дедуп по нижнему регистру после strip и снятия ведущего @.
        Порядок — сортировка по slug (без учёта регистра). Возвращаем без ведущего @.
        """
        async with self._connect() as db:
            conds = [
                "username IS NOT NULL",
                "TRIM(COALESCE(username, '')) != ''",
            ]
            params: list = []
            if category and str(category).strip() and category != "all":
                conds.append("category = ?")
                params.append(category)
            if exclude_username_not_found:
                conds.append(
                    "(username_not_found_at IS NULL OR username_not_found_at = '')"
                )
            sql = f"SELECT username FROM users WHERE {' AND '.join(conds)}"
            cursor = await db.execute(sql, tuple(params))
            rows = await cursor.fetchall()
        seen: dict[str, str] = {}
        for row in rows:
            raw = row[0] if row else None
            if raw is None:
                continue
            s = str(raw).strip().lstrip("@")
            if not s:
                continue
            key = s.lower()
            if key not in seen:
                seen[key] = s
        return sorted(seen.values(), key=str.casefold)

    async def count_username_not_found(self) -> int:
        """Строки с меткой «username не найден в Telegram»."""
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM users WHERE username_not_found_at IS NOT NULL "
                "AND username_not_found_at != ''"
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def clear_username_not_found_all(self) -> int:
        """Снять метку username_not_found_at у всех записей. Возвращает число обновлённых строк."""
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM users WHERE username_not_found_at IS NOT NULL "
                "AND username_not_found_at != ''"
            )
            row = await cur.fetchone()
            n = int(row[0]) if row else 0
            if n == 0:
                return 0
            await db.execute(
                "UPDATE users SET username_not_found_at = NULL "
                "WHERE username_not_found_at IS NOT NULL AND username_not_found_at != ''"
            )
            await db.commit()
            return n

    async def count_privacy_queue(
        self,
        username_contains: str | None = None,
        category: str | None = None,
    ) -> int:
        """Строки в очереди privacy: блок приватности, рассылка ещё не успешна."""
        async with self._connect() as db:
            conds = [
                "(broadcast_privacy_blocked_at IS NOT NULL AND broadcast_privacy_blocked_at != '')",
                "(broadcast_at IS NULL OR broadcast_at = '')",
            ]
            params: list = []
            if username_contains and str(username_contains).strip():
                term = f"%{str(username_contains).strip().lstrip('@').lower()}%"
                conds.append("LOWER(COALESCE(username, '')) LIKE ?")
                params.append(term)
            if category and str(category).strip() and category != "all":
                conds.append("category = ?")
                params.append(category)
            sql = f"SELECT COUNT(*) FROM users WHERE {' AND '.join(conds)}"
            cursor = await db.execute(sql, tuple(params))
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def list_privacy_queue_page(
        self,
        username_contains: str | None = None,
        category: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[dict]:
        """Страница очереди privacy для просмотра."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            conds = [
                "(broadcast_privacy_blocked_at IS NOT NULL AND broadcast_privacy_blocked_at != '')",
                "(broadcast_at IS NULL OR broadcast_at = '')",
            ]
            params: list = []
            if username_contains and str(username_contains).strip():
                term = f"%{str(username_contains).strip().lstrip('@').lower()}%"
                conds.append("LOWER(COALESCE(username, '')) LIKE ?")
                params.append(term)
            if category and str(category).strip() and category != "all":
                conds.append("category = ?")
                params.append(category)
            params.extend([limit, max(0, offset)])
            sql = (
                f"SELECT id, telegram_id, username, category, first_seen_at, broadcast_privacy_blocked_at, metadata "
                f"FROM users WHERE {' AND '.join(conds)} ORDER BY id LIMIT ? OFFSET ?"
            )
            cursor = await db.execute(sql, tuple(params))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_broadcast_sent_today_utc(self, session_name: str) -> int:
        """Число успешных рассылок с аккаунта за текущие сутки UTC."""
        day = datetime.now(timezone.utc).date().isoformat()
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT sent FROM account_broadcast_daily WHERE session_name = ? AND day_utc = ?",
                (session_name, day),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def increment_broadcast_sent_today_utc(self, session_name: str) -> None:
        """+1 к счётчику успешных отправок за сегодня UTC."""
        day = datetime.now(timezone.utc).date().isoformat()
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO account_broadcast_daily (session_name, day_utc, sent) VALUES (?, ?, 1)
                ON CONFLICT(session_name, day_utc) DO UPDATE SET sent = sent + 1
                """,
                (session_name, day),
            )
            await db.commit()

    async def save_checkpoint(self, group_id: str, message_id: int, users_count: int) -> None:
        """Сохранить checkpoint."""
        path = self.db_path.parent / "checkpoint.json"
        data = {
            "last_group_id": group_id,
            "last_message_id": message_id,
            "users_collected": users_count,
            "timestamp": datetime.now().isoformat(),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def load_checkpoint(self) -> dict | None:
        """Загрузить checkpoint."""
        path = self.db_path.parent / "checkpoint.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    async def count_users(self, category: str | None = None) -> tuple[int, int]:
        """Подсчёт: (hot_count, warm_count)."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT category, COUNT(*) FROM users GROUP BY category",
            )
            rows = await cursor.fetchall()
            counts = {row[0]: row[1] for row in rows}
            return (counts.get("hot", 0), counts.get("warm", 0))

    async def count_users_search(
        self,
        username_contains: str | None = None,
        category: str | None = None,
    ) -> int:
        """Число строк users с опциональным фильтром по подстроке username (без @, регистронезависимо)."""
        async with self._connect() as db:
            conds = ["1=1"]
            params: list = []
            if username_contains and str(username_contains).strip():
                term = f"%{str(username_contains).strip().lstrip('@').lower()}%"
                conds.append("LOWER(COALESCE(username, '')) LIKE ?")
                params.append(term)
            if category and str(category).strip() and category != "all":
                conds.append("category = ?")
                params.append(category)
            sql = f"SELECT COUNT(*) FROM users WHERE {' AND '.join(conds)}"
            cursor = await db.execute(sql, tuple(params))
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def list_users_search_page(
        self,
        username_contains: str | None = None,
        category: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[dict]:
        """Страница users для просмотра/поиска."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            conds = ["1=1"]
            params: list = []
            if username_contains and str(username_contains).strip():
                term = f"%{str(username_contains).strip().lstrip('@').lower()}%"
                conds.append("LOWER(COALESCE(username, '')) LIKE ?")
                params.append(term)
            if category and str(category).strip() and category != "all":
                conds.append("category = ?")
                params.append(category)
            params.extend([limit, max(0, offset)])
            sql = (
                f"SELECT id, telegram_id, username, category, first_seen_at, metadata "
                f"FROM users WHERE {' AND '.join(conds)} ORDER BY id LIMIT ? OFFSET ?"
            )
            cursor = await db.execute(sql, tuple(params))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


def session_names_with_any_broadcast_sent(db_path: str | Path | None = None) -> frozenset[str]:
    """
    Синхронно: session_name, с которых уже была успешная рассылка (account_broadcast_daily.sent > 0).
    Таким аккаунтам не меняют прокси при round-robin — риск AuthKeyDuplicatedError при смене IP.
    """
    path = Path(db_path or Path(__file__).parent.parent.parent / "output" / "vibe_marketing.db")
    if not path.is_file():
        return frozenset()
    conn = sqlite3.connect(str(path), timeout=15.0)
    try:
        cur = conn.execute(
            "SELECT DISTINCT session_name FROM account_broadcast_daily "
            "WHERE COALESCE(sent, 0) > 0"
        )
        return frozenset(
            str(row[0]).strip()
            for row in cur.fetchall()
            if row[0] and str(row[0]).strip()
        )
    finally:
        conn.close()


_db: Database | None = None


def get_db() -> Database:
    """Получить экземпляр БД."""
    global _db
    if _db is None:
        _db = Database()
    return _db
