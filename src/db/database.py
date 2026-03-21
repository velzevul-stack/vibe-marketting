"""SQLite база данных."""
import json
from datetime import datetime
from pathlib import Path

import aiosqlite

from src.db.belarus_filter import user_row_matches_belarus


class Database:
    """Работа с БД."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or Path(__file__).parent.parent.parent / "output" / "vibe_marketing.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        """Создать таблицы."""
        async with aiosqlite.connect(self.db_path) as db:
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
                    metadata TEXT,
                    UNIQUE(telegram_id, username)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_category ON users(category)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
            await db.commit()

    async def add_chat(self, telegram_id: str, title: str, link: str, members_count: int = 0, source: str = "manual") -> None:
        """Добавить чат."""
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
    ) -> list[dict]:
        """Получить пользователей."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            conds = ["1=1"]
            params = []
            if exclude_invited:
                conds.append("(invited_to_channel_at IS NULL OR invited_to_channel_at = '')")
            if exclude_added_to_contacts:
                conds.append("(added_to_contacts_at IS NULL OR added_to_contacts_at = '')")
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
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET added_to_contacts_at = ? WHERE id = ?",
                (datetime.now().isoformat(), user_id),
            )
            await db.commit()

    async def mark_invited(self, user_id: int) -> None:
        """Отметить приглашение в канал."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET invited_to_channel_at = ? WHERE id = ?",
                (datetime.now().isoformat(), user_id),
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
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT category, COUNT(*) FROM users GROUP BY category",
            )
            rows = await cursor.fetchall()
            counts = {row[0]: row[1] for row in rows}
            return (counts.get("hot", 0), counts.get("warm", 0))

    async def preview_belarus_user_purge(self) -> tuple[int, int]:
        """
        Сколько записей users удалились бы эвристикой РБ (username + metadata).
        Возвращает (будет_удалено, останется).
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT username, metadata FROM users")
            rows = await cursor.fetchall()
        n_drop = sum(
            1 for r in rows if not user_row_matches_belarus(r["username"], r["metadata"])
        )
        return n_drop, len(rows) - n_drop

    async def purge_users_without_belarus_signals(self) -> tuple[int, int]:
        """
        Удалить из users записи, в username+metadata которых нет эвристики «Беларусь»
        (маркеры + города из data/cities_by.json). Возвращает (удалено, оставлено).
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT id, username, metadata FROM users")
            rows = await cursor.fetchall()
            to_delete: list[int] = []
            for row in rows:
                if not user_row_matches_belarus(row["username"], row["metadata"]):
                    to_delete.append(int(row["id"]))
            for uid in to_delete:
                await db.execute("DELETE FROM users WHERE id = ?", (uid,))
            await db.commit()
            kept = len(rows) - len(to_delete)
            return len(to_delete), kept

    async def count_users_search(
        self,
        username_contains: str | None = None,
        category: str | None = None,
    ) -> int:
        """Число строк users с опциональным фильтром по подстроке username (без @, регистронезависимо)."""
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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


_db: Database | None = None


def get_db() -> Database:
    """Получить экземпляр БД."""
    global _db
    if _db is None:
        _db = Database()
    return _db
