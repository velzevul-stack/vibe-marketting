"""Консольное меню с rich."""
import asyncio
import json
import random
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.prompt import Prompt, Confirm

from src.config import Settings, load_accounts, load_proxies
from src.db import get_db
from src.search import search_groups, load_manual_groups_as_list
from src.verify.scraper import scrape_group
from src.invite import InviteManager, AccountPool

console = Console()


def _load_telegram_index_key() -> str | None:
    """Загрузить API ключ Telegram Index."""
    return Settings().telegram_index_api_key


def _braille_to_ascii(text: str) -> str:
    """Заменяет Braille (⣿⣷) на ASCII по плотности: # * : ."""
    _table = [" ", ".", ":", "*", "O", "@", "#", "#", "#"]

    def dots(c: str) -> int:
        if not ("\u2800" <= c <= "\u28ff"):
            return -1
        return bin(ord(c) - 0x2800).count("1")

    return "".join(_table[min(dots(c), 8)] if dots(c) >= 0 else c for c in text)


def _load_header_art() -> str:
    """Загрузить арт. Приоритет: art_ansi.txt > art.txt > art_ascii.txt. Braille → ASCII."""
    root = Path(__file__).parent.parent.parent
    ansi_path = root / "art_ansi.txt"
    art_path = root / "art.txt"
    ascii_path = root / "art_ascii.txt"
    if ansi_path.exists():
        return ansi_path.read_text(encoding="utf-8").strip()
    if art_path.exists():
        content = art_path.read_text(encoding="utf-8").strip()
        if any("\u2800" <= c <= "\u28ff" for c in content):
            content = _braille_to_ascii(content)
        return content
    if ascii_path.exists():
        return ascii_path.read_text(encoding="utf-8").strip()
    return "[bold cyan]Vibe Marketing[/] - Telegram Lead Scraper"


def _render_main_menu() -> str:
    """Главное меню."""
    header = _load_header_art()
    try:
        console.print(Panel.fit(header, border_style="cyan"))
    except UnicodeEncodeError:
        console.print(Panel.fit(
            "[bold cyan]Vibe Marketing[/] - Telegram Lead Scraper",
            border_style="cyan",
        ))
    console.print()
    console.print("[1] Поиск групп")
    console.print("[2] Сбор базы пользователей")
    console.print("[3] Вступить в группы")
    console.print("[4] Добавить в контакты")
    console.print("[5] Пригласить в канал")
    console.print("[6] Статистика базы")
    console.print("[7] Назначить прокси аккаунтам")
    console.print("[0] Выход")
    console.print()
    return Prompt.ask("Выберите действие", choices=["0", "1", "2", "3", "4", "5", "6", "7"], default="0")


async def _run_search() -> None:
    """Поиск групп."""
    console.print("[bold blue]Поиск групп...[/]")
    api_key = _load_telegram_index_key()
    s = Settings()
    sources = []
    if s.telegram_index_api_key:
        sources.append("RapidAPI")
    if s.tgstat_token:
        sources.append("TGStat")
    if s.telemetr_api_key:
        sources.append("Telemetr")
    if s.tg_catalog_enabled:
        sources.append("TG Catalog")
    if s.ddgs_search_enabled:
        sources.append("DuckDuckGo")
    sources.append("groups.txt")
    if not any([s.telegram_index_api_key, s.tgstat_token, s.telemetr_api_key]):
        console.print("[yellow]API-ключи (RapidAPI/TGStat/Telemetr) не заданы. Используются бесплатные источники.[/]")
    console.print(f"[dim]Источники: {' + '.join(sources)}[/]")
    groups = await search_groups(api_key)
    out_path = Path("output") / "found_groups.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]Найдено групп: {len(groups)}[/]")
    console.print(f"[dim]Сохранено в {out_path}[/]")


async def _run_scrape() -> None:
    """Сбор базы пользователей."""
    db = get_db()
    await db.init()

    groups_path = Path("output") / "found_groups.json"
    manual = load_manual_groups_as_list()
    if groups_path.exists():
        groups = json.loads(groups_path.read_text(encoding="utf-8"))
    else:
        groups = manual

    if not groups:
        console.print("[red]Нет групп для парсинга. Сначала выполните поиск или добавьте группы в config/groups.txt[/]")
        return

    console.print(f"[bold blue]Сбор базы из {len(groups)} групп[/]")
    limit = int(Prompt.ask("Лимит сообщений на группу", default="300"))

    pool = AccountPool()
    max_concurrent = max(1, len(pool.accounts))
    sem = asyncio.Semaphore(max_concurrent)

    async def _scrape_one(i: int, g: dict):
        link = g.get("link") or g.get("id", "")
        if not link or "t.me" not in str(link):
            return 0, 0
        title = g.get("title", "?")
        async with sem:
            try:
                def on_progress(cur, tot):
                    pct = (cur / tot * 100) if tot else 0
                    console.print(f"  [dim]{title}: {cur}/{tot} ({pct:.1f}%)[/]", end="\r")
                hot, warm = await scrape_group(link, limit=limit, pool=pool, on_progress=on_progress)
                console.print(f"  [green]{title}: {hot} горячих, {warm} тёплых[/]")
                return hot, warm
            except Exception as e:
                console.print(f"  [red]{title}: Ошибка {e}[/]")
                return 0, 0
            finally:
                await asyncio.sleep(2)

    tasks = [_scrape_one(i, g) for i, g in enumerate(groups)]
    results = await asyncio.gather(*tasks)
    total_hot = sum(r[0] for r in results)
    total_warm = sum(r[1] for r in results)
    console.print(f"\n[bold green]Итого: {total_hot} горячих, {total_warm} тёплых[/]")


async def _run_join_groups() -> None:
    """Вступить в группы из found_groups.json."""
    groups_path = Path("output") / "found_groups.json"
    if not groups_path.exists():
        console.print("[red]Нет found_groups.json. Сначала выполните поиск групп.[/]")
        return
    groups = json.loads(groups_path.read_text(encoding="utf-8"))
    if not groups:
        console.print("[yellow]Список групп пуст.[/]")
        return
    count = int(Prompt.ask("Сколько групп обработать", default=str(min(10, len(groups)))))
    groups = groups[:count]
    mgr = InviteManager()
    sett = mgr.settings
    ok_count = 0
    for i, g in enumerate(groups):
        link = g.get("link") or g.get("id", "")
        if not link or "t.me" not in str(link):
            continue
        title = g.get("title", "?")
        console.print(f"  [{i+1}/{len(groups)}] {title}...")
        try:
            ok = await mgr.join_group(link)
            if ok:
                ok_count += 1
                console.print(f"    [green]OK[/]")
            else:
                console.print(f"    [red]FAIL[/]")
        except Exception as e:
            console.print(f"    [red]Ошибка: {e}[/]")
        delay = max(1, random.uniform(sett.delay_join_min, sett.delay_join_max))
        await asyncio.sleep(delay)
    console.print(f"\n[bold green]Вступили в {ok_count} из {len(groups)} групп[/]")


async def _run_add_contacts() -> None:
    """Добавить в контакты."""
    db = get_db()
    await db.init()
    cat = Prompt.ask("Категория (hot/warm/all)", choices=["hot", "warm", "all"], default="hot")
    users = await db.get_users(
        category=cat if cat != "all" else None,
        limit=50,
        exclude_added_to_contacts=True,
    )
    if not users:
        console.print("[yellow]Нет пользователей для добавления.[/]")
        return
    count = int(Prompt.ask("Сколько добавить", default=str(min(10, len(users)))))
    users = users[:count]
    mgr = InviteManager()
    for u in users:
        uname = u.get("username") or (f"@{u.get('telegram_id')}" if u.get("telegram_id") else None)
        if not uname:
            continue
        ident = str(uname).lstrip("@")
        if not ident.isdigit():  # AddContact по username, не по id
            console.print(f"  Добавляю @{ident}...")
            ok = await mgr.add_to_contacts(ident)
            if ok:
                await db.mark_added_to_contacts(u["id"])
            console.print(f"    {'[green]OK[/]' if ok else '[red]FAIL[/]'}")
            delay = max(1, random.uniform(mgr.settings.delay_contact_min, mgr.settings.delay_contact_max))
            await asyncio.sleep(delay)


async def _run_invite() -> None:
    """Пригласить в канал — напрямую из контактов аккаунта."""
    channel = Prompt.ask("Username канала/группы (например @channel)")
    channel = channel.lstrip("@").strip()
    if not channel:
        console.print("[red]Укажите username канала.[/]")
        return
    limit = int(Prompt.ask("Сколько контактов пригласить", default="20"))
    console.print(f"[dim]Берём контакты из аккаунта и добавляем в @{channel}[/]")
    if not Confirm.ask("Продолжить?"):
        return
    mgr = InviteManager()
    invited, session = await mgr.invite_contacts_to_channel(
        f"@{channel}", limit=limit, batch_size=10
    )
    console.print(f"\n[bold green]Приглашено: {invited} контактов[/] (аккаунт: {session or '—'})")


def _run_assign_proxies() -> None:
    """Назначить прокси из пула аккаунтам (перестроить под TG-аккаунты)."""
    accounts = load_accounts()
    proxies = load_proxies()
    if not accounts:
        console.print("[red]Нет аккаунтов в config/accounts.json[/]")
        return
    if not proxies:
        console.print("[red]Нет прокси. Добавьте в config/proxies.txt или settings.json[/]")
        return
    if not Confirm.ask(f"Назначить {len(proxies)} прокси для {len(accounts)} аккаунтов?"):
        return
    for i, acc in enumerate(accounts):
        acc["proxy"] = proxies[i % len(proxies)]
    path = Path(__file__).parent.parent / "config" / "accounts.json"
    path.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]Прокси назначены. Сохранено в {path}[/]")


async def _run_stats() -> None:
    """Статистика базы."""
    db = get_db()
    await db.init()
    hot, warm = await db.count_users()
    table = Table(title="Статистика базы")
    table.add_column("Категория", style="cyan")
    table.add_column("Количество", style="green")
    table.add_row("Горячие", str(hot))
    table.add_row("Тёплые", str(warm))
    table.add_row("Всего", str(hot + warm))
    console.print(table)


def run_menu() -> None:
    """Запуск главного меню."""
    while True:
        choice = _render_main_menu()
        if choice == "0":
            break
        try:
            if choice == "1":
                asyncio.run(_run_search())
            elif choice == "2":
                asyncio.run(_run_scrape())
            elif choice == "3":
                asyncio.run(_run_join_groups())
            elif choice == "4":
                asyncio.run(_run_add_contacts())
            elif choice == "5":
                asyncio.run(_run_invite())
            elif choice == "6":
                asyncio.run(_run_stats())
            elif choice == "7":
                _run_assign_proxies()
        except KeyboardInterrupt:
            console.print("\n[yellow]Прервано.[/]")
        except Exception as e:
            console.print(f"[red]Ошибка: {e}[/]")
        console.print()
