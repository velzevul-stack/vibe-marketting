"""Умное распределение аккаунтов и приглашения."""
import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.contacts import AddContactRequest, GetContactsRequest
from telethon.tl.functions.channels import InviteToChannelRequest, JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from src.config import load_accounts, load_proxies, Settings


@dataclass
class AccountState:
    """Состояние аккаунта для умного распределения."""
    session_name: str
    actions_today: int = 0
    last_action_at: float = 0
    flood_wait_until: float = 0
    is_available: bool = True


class AccountPool:
    """Умное распределение между аккаунтами и прокси."""

    def __init__(self):
        self.accounts: list[AccountState] = []
        self._proxy_pool: list[str] = []
        self._proxy_index: int = 0
        self._load()

    def _load(self) -> None:
        """Загрузить аккаунты и пул прокси."""
        accs = load_accounts()
        for a in accs:
            self.accounts.append(AccountState(session_name=a.get("session_name", "default")))
        self._proxy_pool = load_proxies()

    def _get_next_proxy(self) -> str | None:
        """Следующий прокси из пула (round-robin)."""
        if not self._proxy_pool:
            return None
        proxy = self._proxy_pool[self._proxy_index % len(self._proxy_pool)]
        self._proxy_index += 1
        return proxy

    def get_best_account(self) -> AccountState | None:
        """Выбрать аккаунт с наименьшей нагрузкой (least-used-first, FloodWait-aware)."""
        now = time.time()
        available = [
            a for a in self.accounts
            if a.is_available and a.flood_wait_until < now
        ]
        if not available:
            return None
        # Сортировка: меньше действий сегодня, дольше не использовался
        return min(
            available,
            key=lambda x: (x.actions_today, x.last_action_at),
        )

    def mark_used(self, session_name: str) -> None:
        """Отметить использование аккаунта."""
        for a in self.accounts:
            if a.session_name == session_name:
                a.actions_today += 1
                a.last_action_at = time.time()
                break

    def mark_flood_wait(self, session_name: str, wait_seconds: int) -> None:
        """Исключить аккаунт из пула до истечения FloodWait."""
        for a in self.accounts:
            if a.session_name == session_name:
                a.flood_wait_until = time.time() + wait_seconds
                break

    def get_client(
        self, session_name: str, prefer_pool_for_read: bool = False
    ) -> TelegramClient | None:
        """
        Создать клиент для аккаунта.
        Прокси: при prefer_pool_for_read (поиск/сбор) — из пула; иначе из аккаунта или пула.
        """
        accs = load_accounts()
        acc = next((a for a in accs if a.get("session_name") == session_name), None)
        if not acc:
            return None
        api_id = acc.get("api_id")
        api_hash = acc.get("api_hash")
        if not api_id or not api_hash:
            return None
        session_path = Path("sessions") / f"{session_name}.session"
        session_path.parent.mkdir(exist_ok=True)
        proxy = None
        if prefer_pool_for_read and self._proxy_pool:
            proxy = self._get_next_proxy()
        if not proxy:
            proxy = acc.get("proxy")
        if not proxy and self._proxy_pool:
            proxy = self._get_next_proxy()
        return TelegramClient(
            str(session_path),
            api_id,
            api_hash,
            proxy=proxy,
        )


def smart_delay(min_sec: int, max_sec: int) -> float:
    """Случайная задержка в секундах (±20%)."""
    base = random.uniform(min_sec, max_sec)
    jitter = base * 0.2 * (random.random() - 0.5)
    return max(1, base + jitter)


class InviteManager:
    """Управление приглашениями с умным распределением."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.pool = AccountPool()

    async def add_to_contacts(self, username: str) -> bool:
        """Добавить пользователя в контакты."""
        state = self.pool.get_best_account()
        if not state:
            return False
        client = self.pool.get_client(state.session_name)
        if not client:
            return False
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return False
            username_clean = username.lstrip("@")
            await client(AddContactRequest(
                id=username_clean,
                first_name=username_clean,
                last_name="",
                phone="",
            ))
            self.pool.mark_used(state.session_name)
            return True
        except FloodWaitError as e:
            self.pool.mark_flood_wait(state.session_name, e.seconds)
            await asyncio.sleep(e.seconds)
            return await self.add_to_contacts(username)
        except Exception:
            return False
        finally:
            await client.disconnect()

    async def join_group(self, link: str) -> bool:
        """Вступить в группу/канал по ссылке. Поддержка публичных и приватных (joinchat)."""
        state = self.pool.get_best_account()
        if not state:
            return False
        client = self.pool.get_client(state.session_name)
        if not client:
            return False
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return False
            link = (link or "").strip()
            if "joinchat/" in link.lower():
                match = re.search(r"joinchat/([a-zA-Z0-9_-]+)", link, re.I)
                if match:
                    hash_part = match.group(1)
                    await client(ImportChatInviteRequest(hash_part))
                else:
                    return False
            else:
                entity = await client.get_entity(link)
                await client(JoinChannelRequest(entity))
            self.pool.mark_used(state.session_name)
            return True
        except FloodWaitError as e:
            self.pool.mark_flood_wait(state.session_name, e.seconds)
            await asyncio.sleep(e.seconds)
            return await self.join_group(link)
        except Exception:
            return False
        finally:
            await client.disconnect()

    async def invite_contacts_to_channel(
        self, channel_username: str, limit: int = 50, batch_size: int = 10
    ) -> tuple[int, str]:
        """
        Добавить в канал контакты аккаунта напрямую.
        Берёт контакты из аккаунта (least-used) и приглашает их в канал.
        Возвращает (приглашено, session_name).
        """
        state = self.pool.get_best_account()
        if not state:
            return 0, ""
        client = self.pool.get_client(state.session_name)
        if not client:
            return 0, ""
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return 0, ""
            result = await client(GetContactsRequest(hash=0))
            users = getattr(result, "users", []) or []
            users = [u for u in users if not getattr(u, "bot", False)]
            users = users[:limit]
            if not users:
                return 0, state.session_name
            channel = await client.get_entity(channel_username)
            invited = 0
            for i in range(0, len(users), batch_size):
                batch = users[i : i + batch_size]
                try:
                    await client(InviteToChannelRequest(channel, batch))
                    invited += len(batch)
                    self.pool.mark_used(state.session_name)
                except FloodWaitError as e:
                    self.pool.mark_flood_wait(state.session_name, e.seconds)
                    await asyncio.sleep(e.seconds)
                    try:
                        await client(InviteToChannelRequest(channel, batch))
                        invited += len(batch)
                        self.pool.mark_used(state.session_name)
                    except Exception:
                        pass
                except Exception:
                    pass
            return invited, state.session_name
        except Exception:
            return 0, state.session_name
        finally:
            await client.disconnect()

    async def invite_to_channel(
        self, channel_username: str, users: list[dict]
    ) -> tuple[int, list[int]]:
        """
        Пригласить пользователей в канал (по telegram_id/username).
        users: list[dict] с полями telegram_id, username, id (db id).
        Возвращает (приглашено, список db id приглашённых).
        """
        if not users:
            return 0, []
        state = self.pool.get_best_account()
        if not state:
            return 0, []
        client = self.pool.get_client(state.session_name)
        if not client:
            return 0, []
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return 0, []
            channel = await client.get_entity(channel_username)
            entities = []
            invited_ids = []
            for u in users:
                try:
                    uid = u.get("telegram_id")
                    uname = (u.get("username") or "").lstrip("@")
                    if uid and str(uid).isdigit():
                        ent = await client.get_entity(int(uid))
                    elif uname:
                        ent = await client.get_entity(uname)
                    else:
                        continue
                    entities.append(ent)
                    invited_ids.append(u.get("id"))
                except Exception:
                    continue
            if not entities:
                return 0, []
            await client(InviteToChannelRequest(channel, entities))
            self.pool.mark_used(state.session_name)
            return len(entities), invited_ids
        except FloodWaitError as e:
            self.pool.mark_flood_wait(state.session_name, e.seconds)
            await asyncio.sleep(e.seconds)
            return await self.invite_to_channel(channel_username, users)
        except Exception:
            return 0, []
        finally:
            await client.disconnect()
