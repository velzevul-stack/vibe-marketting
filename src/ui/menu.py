"""Консольное меню с rich."""
import asyncio
import json
import random
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.prompt import Prompt, Confirm

from src.config import (
    Settings,
    assign_proxies_round_robin_to_accounts,
    load_accounts,
    load_proxies,
    mask_proxy_display,
)
from src.db import get_db
from src.search import search_groups, load_manual_groups_as_list
from src.verify.scraper import scrape_group
from src.verify.proxy_checker import check_proxies
from src.invite import InviteManager, AccountPool
from src.telethon_session_menu import run_telethon_session_menu
from src.accounts_bulk_prepare import run_bulk_account_prepare

console = Console()


def _mi(label: str) -> str:
    """Пункт меню для Rich: [[n]] → отображается как [n] (одинарные [ — разметка Rich)."""
    return f"[[{label}]]"


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
    console.print("[bold]Данные и поиск[/]")
    console.print(f"{_mi('1')} Поиск групп")
    console.print(f"{_mi('2')} Сбор базы пользователей")
    console.print(f"{_mi('7')} Просмотр найденных групп")
    console.print(f"{_mi('6')} Статистика базы")
    console.print()
    console.print("[bold]Действия в Telegram[/]")
    console.print(f"{_mi('3')} Вступить в группы")
    console.print(f"{_mi('4')} Добавить в контакты")
    console.print(f"{_mi('5')} Пригласить в канал")
    console.print()
    console.print(f"{_mi('8')} Прокси, сессии и аккаунты…")
    console.print(f"{_mi('0')} Выход")
    console.print()
    return Prompt.ask(
        "Выберите действие",
        choices=["0", "1", "2", "3", "4", "5", "6", "7", "8"],
        default="0",
    )


def _run_proxy_session_submenu() -> None:
    """Подменю: прокси, сессии, массовая подготовка."""
    while True:
        console.print()
        console.print("[bold cyan]Прокси, сессии и аккаунты[/]")
        console.print(f"{_mi('1')} Назначить прокси аккаунтам (из пула → accounts.json)")
        console.print(f"{_mi('2')} Проверить прокси")
        console.print(f"{_mi('3')} Сессии Telethon (.session) — список, импорт, вход")
        console.print(f"{_mi('4')} Подготовка аккаунтов: 2FA → прокси → сброс чужих сессий")
        console.print(f"{_mi('0')} Назад в главное меню")
        console.print()
        sub = Prompt.ask(
            "Выбор",
            choices=["0", "1", "2", "3", "4"],
            default="0",
        )
        if sub == "0":
            break
        try:
            if sub == "1":
                _run_assign_proxies()
            elif sub == "2":
                asyncio.run(_run_check_proxies())
            elif sub == "3":
                asyncio.run(run_telethon_session_menu(console))
            elif sub == "4":
                asyncio.run(run_bulk_account_prepare(console))
        except KeyboardInterrupt:
            console.print("\n[yellow]Прервано.[/]")
        except Exception as e:
            console.print(f"[red]Ошибка: {e}[/]")


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
    console.print()

    progress_state = {"source": "", "query": "", "cur": 0, "total": 1, "found": 0, "proxy": ""}
    live_ref: list = []

    def make_panel() -> Panel:
        src = progress_state["source"]
        q = progress_state["query"]
        cur = progress_state["cur"]
        tot = progress_state["total"]
        found = progress_state["found"]
        proxy = progress_state["proxy"]
        pct = (cur / tot * 100) if tot else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        proxy_line = f"[dim]Прокси:[/] [yellow]{proxy}[/]\n" if proxy else ""
        return Panel(
            f"[cyan]{src}[/]\n"
            f"[dim]Запрос:[/] {q[:60]}{'...' if len(q) > 60 else ''}\n"
            f"{proxy_line}"
            f"[green][{bar}][/] {cur}/{tot} ({pct:.0f}%)\n"
            f"[bold]Найдено групп:[/] [green]{found}[/]",
            title="[bold]Поиск[/]",
            border_style="blue",
        )

    def on_progress(source: str, query: str, cur: int, total: int, found: int, proxy_info: str = "") -> None:
        progress_state.update(source=source, query=query, cur=cur, total=total, found=found, proxy=proxy_info)
        if live_ref:
            live_ref[0].update(make_panel())

    with Live(make_panel(), refresh_per_second=4, console=console, transient=True) as live:
        live_ref.append(live)
        groups = await search_groups(api_key, on_progress=on_progress)
        progress_state["cur"] = progress_state["total"]
        progress_state["found"] = len(groups)
        live.update(make_panel())

    out_path = Path("output") / "found_groups.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"\n[green]Найдено групп: {len(groups)}[/]")
    from collections import Counter
    by_source = Counter(g.get("source", "?") for g in groups)
    for src, cnt in by_source.most_common():
        console.print(f"  [dim]{src}: {cnt}[/]")
    console.print(f"[dim]Сохранено в {out_path}[/]")
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


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
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


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
    n_acc = len(mgr.pool.accounts)
    console.print(
        f"[dim]Аккаунтов: {n_acc}. "
        f"Распределение: least-used (меньше действий сегодня → приоритет). "
        f"Пауза между вступлениями: {sett.delay_join_min}–{sett.delay_join_max} сек (случайно).[/]\n"
    )
    ok_count = 0
    for i, g in enumerate(groups):
        link = g.get("link") or g.get("id", "")
        if not link or "t.me" not in str(link):
            continue
        title = g.get("title", "?")
        console.print(f"  [{i+1}/{len(groups)}] {title}...")
        try:
            ok, session = await mgr.join_group(link)
            who = session or "—"
            if ok:
                ok_count += 1
                console.print(f"    [green]OK[/] [dim](аккаунт: {who})[/]")
            else:
                console.print(f"    [red]FAIL[/] [dim](аккаунт: {who})[/]")
        except Exception as e:
            console.print(f"    [red]Ошибка: {e}[/]")
        delay = max(1, random.uniform(sett.delay_join_min, sett.delay_join_max))
        console.print(f"    [dim]пауза {delay:.0f} сек…[/]")
        await asyncio.sleep(delay)
    console.print(f"\n[bold green]Вступили в {ok_count} из {len(groups)} групп[/]")
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


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
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


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
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


async def _run_check_proxies() -> None:
    """Проверить работоспособность прокси из пула."""
    proxies = load_proxies()
    if not proxies:
        console.print("[red]Нет прокси. Добавьте в config/proxies.txt или settings.json[/]")
        Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")
        return

    console.print(f"[bold blue]Проверка {len(proxies)} прокси...[/]")
    console.print(
        "[dim]Цепочка: ipify → jsonip → httpbin → api.telegram.org (404 на корне TG = ОК). "
        "Таймаут 15 сек. Разные сайты по-разному относятся к прокси.[/]\n"
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Проверка...", total=len(proxies))
        results = await check_proxies(proxies, max_concurrent=10)
        progress.update(task, completed=len(proxies))

    ok_list = [r for r in results if r.ok]
    fail_list = [r for r in results if not r.ok]

    table = Table(title="Результаты проверки прокси")
    table.add_column("#", style="dim", width=4)
    table.add_column("Прокси", style="cyan")
    table.add_column("Статус", style="green")
    table.add_column("Задержка / Ошибка", style="white")

    for i, r in enumerate(results, 1):
        disp = mask_proxy_display(r.proxy)
        status = "[green]OK[/]" if r.ok else "[red]FAIL[/]"
        if r.ok and r.latency_ms is not None:
            host = urlparse(r.check_url).netloc if r.check_url else "?"
            extra = f"{r.latency_ms:.0f} мс · [dim]{host}[/]"
        else:
            extra = r.error or "—"
        table.add_row(str(i), disp, status, extra)

    console.print(table)
    console.print(f"\n[bold green]Рабочих: {len(ok_list)}[/] | [bold red]Не работают: {len(fail_list)}[/]")
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


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
    ok, msg = assign_proxies_round_robin_to_accounts()
    if ok:
        console.print(f"[green]Прокси назначены. Сохранено в {msg}[/]")
    else:
        console.print(f"[red]{msg}[/]")
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


async def _run_stats() -> None:
    """Статистика базы."""
    db = get_db()
    await db.init()
    hot, warm = await db.count_users()

    # Найденные группы
    found_groups_path = Path("output") / "found_groups.json"
    found_count = 0
    by_source = {}
    if found_groups_path.exists():
        try:
            groups = json.loads(found_groups_path.read_text(encoding="utf-8"))
            found_count = len(groups) if isinstance(groups, list) else 0
            from collections import Counter
            by_source = Counter(g.get("source", "?") for g in groups) if isinstance(groups, list) else {}
        except Exception:
            pass

    table = Table(title="Статистика")
    table.add_column("Категория", style="cyan")
    table.add_column("Количество", style="green")
    table.add_row("[bold]Найденные группы[/]", str(found_count))
    if by_source:
        for src, cnt in sorted(by_source.items(), key=lambda x: -x[1]):
            table.add_row(f"  [dim]{src}[/]", str(cnt))
    table.add_row("", "")
    table.add_row("[bold]Продавцы в базе[/]", "")
    table.add_row("  Горячие", str(hot))
    table.add_row("  Тёплые", str(warm))
    table.add_row("  Всего", str(hot + warm))
    console.print(table)
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


def _run_view_groups() -> None:
    """Просмотр найденных групп из found_groups.json."""
    found_path = Path("output") / "found_groups.json"
    if not found_path.exists():
        console.print("[red]Нет found_groups.json. Сначала выполните поиск групп (п.1).[/]")
        return
    try:
        groups = json.loads(found_path.read_text(encoding="utf-8"))
    except Exception as e:
        console.print(f"[red]Ошибка чтения: {e}[/]")
        return
    if not isinstance(groups, list) or not groups:
        console.print("[yellow]Список групп пуст.[/]")
        return
    limit = int(Prompt.ask("Сколько показать (0 = все)", default="30"))
    if limit <= 0:
        limit = len(groups)
    show = groups[:limit]
    table = Table(title=f"Найденные группы (показано {len(show)} из {len(groups)})")
    table.add_column("#", style="dim", width=4)
    table.add_column("Источник", style="cyan", width=12)
    table.add_column("Ссылка", style="green")
    table.add_column("Название", style="white")
    for i, g in enumerate(show, 1):
        link = g.get("link", "") or g.get("id", "")
        title = (g.get("title") or "")[:40]
        if len((g.get("title") or "")) > 40:
            title += "..."
        table.add_row(str(i), g.get("source", "?"), link, title)
    console.print(table)
    console.print(f"[dim]Всего групп: {len(groups)}. Файл: {found_path}[/]")
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


def run_menu() -> None:
    """Запуск главного меню."""
    _sett = Settings()
    if _sett.assign_proxies_on_startup:
        ok, msg = assign_proxies_round_robin_to_accounts()
        if ok:
            console.print(f"[dim]assign_proxies_on_startup:[/] [green]прокси обновлены[/] → {msg}")
        else:
            console.print(f"[dim]assign_proxies_on_startup:[/] [yellow]{msg}[/]")
        console.print()

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
                _run_view_groups()
            elif choice == "8":
                _run_proxy_session_submenu()
        except KeyboardInterrupt:
            console.print("\n[yellow]Прервано.[/]")
        except Exception as e:
            console.print(f"[red]Ошибка: {e}[/]")
        console.print()
