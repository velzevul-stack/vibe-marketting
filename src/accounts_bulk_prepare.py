"""
Массовая подготовка аккаунтов: облачный 2FA → назначение прокси → сброс чужих сессий.
Пароль 2FA: `bulk_2fa_password` в settings.json или константа в `config.effective_2fa_password`.
"""
from __future__ import annotations

import asyncio
import random

from rich.prompt import Confirm, Prompt
from telethon import TelegramClient
from telethon.tl.functions.account import GetPasswordRequest
from telethon.tl.functions.auth import ResetAuthorizationsRequest

from src.config import (
    Settings,
    assign_proxies_round_robin_to_accounts,
    effective_2fa_password,
    is_placeholder_proxy_url,
    is_proxy_enabled,
    load_accounts,
    load_proxy_pool_from_config,
    proxy_url_to_telethon,
    telethon_session_file,
)


def _client_for(acc: dict, proxy: str | None, settings: Settings) -> TelegramClient:
    name = acc.get("session_name") or "default"
    path = telethon_session_file(name, settings)
    return TelegramClient(
        str(path),
        int(acc["api_id"]),
        str(acc["api_hash"]),
        proxy=proxy_url_to_telethon(proxy),
    )


async def run_bulk_account_prepare(console) -> None:
    """
    1) Включить облачный пароль 2FA (без email), если ещё не включён.
    2) Назначить прокси из пула (как меню 9 → 2 → 2).
    3) Подключиться с прокси и вызвать auth.resetAuthorizations (все другие устройства вылетают).
    """
    settings = Settings()
    accounts = load_accounts()
    if not accounts:
        console.print("[red]Нет аккаунтов в config/accounts.json[/]")
        return

    pwd = settings.bulk_2fa_password
    if not pwd:
        console.print(
            "[dim]Пароль 2FA не задан в settings.json (ключ bulk_2fa_password). "
            "Введите ниже (или отмените Ctrl+C).[/]"
        )
        pwd = Prompt.ask("Пароль облачного 2FA (без email)", password=True)
    if not pwd:
        console.print("[red]Пустой пароль — отмена[/]")
        return

    pool = load_proxy_pool_from_config()
    if not pool:
        console.print("[yellow]В пуле нет прокси — шаг 3 выполнить не получится.[/]")

    console.print(
        "\n[bold]Будет выполнено:[/]\n"
        "  1) Для каждого аккаунта: если 2FA нет — установить указанный пароль (hint пустой, email нет).\n"
        "  2) Назначить прокси из пула всем аккаунтам (round-robin).\n"
        "  3) С прокси: сброс [bold]всех других[/] авторизаций (текущая сессия остаётся).\n"
    )
    if not Confirm.ask("Продолжить?", default=False):
        return

    delay = max(2.0, settings.bulk_prepare_delay_sec)

    # --- Шаг 1: 2FA (без прокси — как у вас сессии лежат локально) ---
    console.print("\n[bold cyan]Шаг 1/3: проверка / установка 2FA[/]")
    for acc in accounts:
        name = acc.get("session_name", "?")
        path = telethon_session_file(name, settings)
        if not path.is_file():
            console.print(f"  [yellow]{name}: нет файла {path} — пропуск[/]")
            continue
        client = _client_for(acc, proxy=None, settings=settings)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                console.print(f"  [red]{name}: сессия не авторизована[/]")
                continue
            pstate = await client(GetPasswordRequest())
            if getattr(pstate, "has_password", False):
                console.print(f"  [dim]{name}: 2FA уже включён — пропуск[/]")
            else:
                await client.edit_2fa(new_password=pwd, hint="", email=None)
                console.print(f"  [green]{name}: 2FA включён[/]")
        except Exception as e:
            console.print(f"  [red]{name}: ошибка 2FA — {e}[/]")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
        await asyncio.sleep(delay + random.uniform(0, 2))

    # --- Шаг 2: прокси ---
    console.print("\n[bold cyan]Шаг 2/3: назначение прокси[/]")
    ok, msg = assign_proxies_round_robin_to_accounts()
    if ok:
        console.print(f"  [green]{msg}[/]")
    else:
        console.print(f"  [red]{msg}[/]")
        if not proxies:
            return

    accounts = load_accounts()

    # --- Шаг 3: сброс чужих сессий с прокси ---
    console.print("\n[bold cyan]Шаг 3/3: сброс других сессий (через прокси аккаунта)[/]")
    for acc in accounts:
        name = acc.get("session_name", "?")
        path = telethon_session_file(name, settings)
        proxy = None
        if is_proxy_enabled():
            proxy = acc.get("proxy")
            if is_placeholder_proxy_url(proxy):
                proxy = None
        if not path.is_file():
            console.print(f"  [yellow]{name}: нет файла сессии — пропуск[/]")
            continue
        if is_proxy_enabled() and not proxy:
            console.print(f"  [yellow]{name}: нет proxy в JSON — пропуск сброса[/]")
            continue
        client = _client_for(acc, proxy=proxy, settings=settings)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                console.print(f"  [red]{name}: не авторизован (проверьте прокси)[/]")
                continue
            await client(ResetAuthorizationsRequest())
            console.print(f"  [green]{name}: другие сессии сброшены (осталась эта)[/]")
        except Exception as e:
            console.print(f"  [red]{name}: сброс сессий — {e}[/]")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
        await asyncio.sleep(delay + random.uniform(0, 2))

    console.print("\n[bold green]Готово. Дальше используйте меню как обычно (прокси уже в accounts.json).[/]")
