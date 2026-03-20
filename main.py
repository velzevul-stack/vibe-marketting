#!/usr/bin/env python3
"""Vibe Marketing CLI — Telegram Lead Scraper для вейп-продавцов."""
import argparse
import sys
from pathlib import Path

# Добавить корень проекта в path
sys.path.insert(0, str(Path(__file__).parent))

# Принудительный UTF-8 для корректного отображения арта везде
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    ctypes.windll.kernel32.SetConsoleCP(65001)

from src.ui.menu import run_menu


def _run_startup_session_sync() -> None:
    from rich.console import Console
    from src.config import Settings
    from src.session_sync import sync_sessions_dir_to_accounts

    s = Settings()
    if not s.sync_sessions_on_startup:
        return
    con = Console()
    try:
        n_add, warns = sync_sessions_dir_to_accounts(s)
        if n_add:
            con.print(
                f"[dim]sync_sessions:[/] [green]+{n_add}[/] аккаунт(ов) → accounts.json"
            )
        for w in warns[:8]:
            con.print(f"[dim]sync_sessions:[/] [yellow]{w}[/]")
    except Exception as e:
        con.print(f"[red]sync_sessions: {e}[/]")


def _cli_proxy_state(state: str) -> int:
    """Включить/выключить прокси в settings.json или показать статус."""
    from rich.console import Console
    from src.config import is_proxy_enabled, set_proxy_enabled

    con = Console()
    if state == "status":
        con.print(
            f"[bold]proxy_enabled:[/] [cyan]{is_proxy_enabled()}[/] "
            "(см. config/settings.json)"
        )
        return 0
    ok, msg = set_proxy_enabled(state == "on")
    if ok:
        con.print(
            f"[green]Прокси {'включены' if state == 'on' else 'выключены'}:[/] "
            f"[dim]{msg}[/] → [bold]proxy_enabled[/] = {state == 'on'}"
        )
        return 0
    con.print(f"[red]{msg}[/]")
    return 1


def _cli_assign_proxies_only() -> int:
    """Только перезаписать proxy в accounts.json из пула (без меню)."""
    from rich.console import Console
    from src.config import (
        assign_proxies_round_robin_to_accounts,
        load_accounts,
        load_proxy_pool_from_config,
    )

    con = Console()
    if not load_accounts():
        con.print("[red]Нет аккаунтов в config/accounts.json[/]")
        return 1
    if not load_proxy_pool_from_config():
        con.print("[red]Нет прокси в пуле[/]")
        return 1
    ok, msg = assign_proxies_round_robin_to_accounts()
    if ok:
        con.print(f"[green]Прокси назначены:[/] {msg}")
        return 0
    con.print(f"[red]{msg}[/]")
    return 1


def main() -> None:
    """Точка входа."""
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="Vibe Marketing CLI — поиск групп Telegram, сбор базы, join/контакты/инвайты.",
        epilog=(
            "Примеры:\n"
            "  python main.py                  интерактивное меню\n"
            "  python main.py --assign-proxies назначить прокси из пула в accounts.json и выйти\n"
            "  python main.py --proxy off      не использовать прокси (поиск, Telethon)\n"
            "  python main.py --proxy on       снова использовать прокси из конфига\n"
            "  python main.py --proxy status   текущее значение proxy_enabled\n"
            "\n"
            "Справка по конфигу: config/CONFIG.md, docs/PROXY_AND_ACCOUNTS.md"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--proxy",
        metavar="STATE",
        choices=["on", "off", "status"],
        help="Вкл/выкл использование прокси (ключ proxy_enabled в settings.json); status — только вывод",
    )
    parser.add_argument(
        "--assign-proxies",
        action="store_true",
        help="Перезаписать proxy у всех аккаунтов из пула (proxies.txt / settings) и выйти",
    )
    args = parser.parse_args()
    if args.proxy is not None:
        raise SystemExit(_cli_proxy_state(args.proxy))
    if args.assign_proxies:
        _run_startup_session_sync()
        raise SystemExit(_cli_assign_proxies_only())
    run_menu()


if __name__ == "__main__":
    main()
