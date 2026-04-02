"""Консольное меню с rich."""
import asyncio
import json
import random
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.prompt import Prompt, Confirm

from src.telethon_account_catalog import (
    build_telethon_catalog,
    format_last_use_line,
    print_account_catalog_table,
    prompt_account_scope,
    prompt_row_indices_scope,
)
from src.config import (
    Settings,
    account_row_for_session_name,
    accounts_json_path,
    assign_apis_fill_missing_in_accounts,
    assign_apis_round_robin_to_accounts,
    assign_proxies_round_robin_to_accounts,
    bundle_round_robin_account_rows,
    clear_accounts_json,
    clear_proxy_pool_in_config,
    clear_telethon_default_api,
    clone_settings,
    group_links_file_path,
    is_proxy_enabled,
    load_accounts,
    load_accounts_all,
    load_api_pairs_from_file,
    load_groups_from_links_txt,
    load_proxy_pool_from_config,
    load_proxy_pool_from_file,
    mask_proxy_display,
    set_proxy_enabled,
    set_telethon_default_api,
    strip_api_credentials_from_accounts,
    telethon_session_dir_path,
    touch_accounts_last_use,
    upsert_telethon_account,
    wipe_telethon_session_files,
)
from src.jobs.launcher import spawn_worker_job
from src.jobs.registry import read_recent_registry_entries, pid_alive
from src.account_zip_import import import_sessions_zip, print_zip_import_report
from src.broadcast.bundle import (
    discover_campaign_import_slices,
    load_campaign_bundle,
    validate_campaign_bundle,
    validate_extra_import_slices,
)
from src.broadcast.campaign_assign import apply_package_api_proxy_assignments
from src.broadcast.csv_cli import run_csv_broadcast_cli
from src.broadcast.runner import run_dm_broadcast
from src.groups_txt_io import export_groups_to_txt, import_txt_to_found_groups, load_found_groups_list
from src.db import get_db
from src.search import search_groups
from src.verify.scraper import normalize_scrape_target, scrape_group
from src.verify.proxy_checker import check_proxies
from src.invite import InviteManager, AccountPool
from src.telethon_session_menu import login_client_for_one_off_scrape, run_telethon_session_menu
from src.accounts_bulk_prepare import run_bulk_account_prepare
from src.session_sync import sync_sessions_dir_to_accounts
from src.cli_input import parse_api_id_digits, parse_nonneg_int_clamped, strip_c0_controls
from src.ui.progress_util import console_loading
from telethon import TelegramClient

console = Console()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_FOUND_GROUPS_PREVIOUS = Path("output") / "found_groups.previous.json"


def _prompt_nonneg_int(
    message: str,
    default: int,
    *,
    allow_zero: bool = False,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """
    Читает неотрицательное целое из консоли. В SSH/backspace часто даёт «^H» в строке —
    оставляем только цифры, иначе берём default.
    """
    raw = Prompt.ask(message, default=str(default))
    return parse_nonneg_int_clamped(
        raw,
        default=default,
        allow_zero=allow_zero,
        minimum=minimum,
        maximum=maximum,
    )


def _emit_zero_search_diagnostics(search_diag: dict, search_fail: str | None) -> None:
    """
    Дублирует диагностику в обычный stdout (flush) и в output/search_diagnostics_last.txt —
    Rich Live(transient) / некоторые SSH/screen режут только Rich-вывод.
    """
    raw = search_diag.get("raw", 0)
    av = search_diag.get("after_vape", 0)
    fin = search_diag.get("final", 0)
    cc = search_diag.get("cities_query_count")
    th = search_diag.get("themes_count")
    nresp = search_diag.get("responses_with_groups", 0)
    err = search_diag.get("first_error")
    finished = search_diag.get("search_finished", False)
    lines = [
        "",
        "========== ДИАГНОСТИКА ПОИСКА (0 групп) ==========",
    ]
    if search_fail:
        lines.append(f"Исключение: {search_fail}")
    elif not finished and not search_diag:
        lines.append(
            "Метрики не собраны (пустой diagnostics — возможно сбой до входа в search_groups)."
        )
    elif not finished:
        lines.append("Поиск не дошёл до конца (search_finished=false).")
    if cc is not None and cc == 0:
        lines.append(
            "0 городов в запросах — проверьте data/cities_by.json и "
            "exclude_russian_cities_in_search / блоклист РФ."
        )
    lines.append(f"Сырых записей до фильтров: {raw}")
    lines.append(f"После вейп-фильтра: {av} → итог: {fin}")
    lines.append(f"Запросов с хотя бы одной группой в ответе: {nresp}")
    if th is not None and cc is not None:
        lines.append(f"Тем в keywords: {th}, городов в запросах: {cc}")
    if raw == 0 and finished:
        lines.append(
            "Подсказка: API ничего не вернули — часто прокси, блок tg-cat/ddgs, сеть."
        )
    elif av == 0 and raw > 0:
        lines.append(
            "Подсказка: всё отсеяно vape_markers — см. config/keywords.json и exclude_keywords.json."
        )
    elif fin == 0 and av > 0:
        lines.append("Подсказка: отсеяно фильтром городов РФ (russian_cities_blocklist).")
    if err:
        lines.append(f"Первая ошибка HTTP/запроса: {err}")

    json_path = Path("output") / "last_search_diagnostics.json"
    txt_path = Path("output") / "search_diagnostics_last.txt"
    try:
        dump = {**search_diag, "menu_search_exception": search_fail}
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
        lines.append(f"JSON: {json_path.resolve()}")
    except OSError as e:
        lines.append(f"(не удалось записать JSON: {e})")

    body = "\n".join(lines) + "\n"
    txt_note = ""
    try:
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(body, encoding="utf-8")
        txt_note = f"Текст: {txt_path.resolve()}\n"
    except OSError:
        pass

    try:
        sys.stdout.write(body + txt_note)
        sys.stdout.flush()
    except OSError:
        pass

    console.print("\n[bold yellow]Диагностика (дубликат в stdout и output/search_diagnostics_last.txt):[/]")
    for ln in lines:
        console.print(f"  [white]{escape(ln)}[/]")
    if txt_note.strip():
        console.print(f"  [dim]{escape(txt_note.strip())}[/]")


_FOUND_GROUPS_ARCHIVE_DIR = Path("output") / "found_groups_archive"


def _snapshot_found_groups_before_overwrite(found_path: Path) -> bool:
    """
    Если found_groups.json есть и в нём непустой список групп — сохранить копию
    в found_groups.previous.json (и дубликат с меткой времени в found_groups_archive/).
    Возвращает True, если снимок записан.
    """
    if not found_path.is_file():
        return False
    try:
        body = found_path.read_text(encoding="utf-8")
        raw = body.strip()
        if not raw:
            return False
        data = json.loads(raw)
        if not isinstance(data, list) or len(data) == 0:
            return False
    except (json.JSONDecodeError, OSError):
        return False
    try:
        found_path.parent.mkdir(parents=True, exist_ok=True)
        _FOUND_GROUPS_PREVIOUS.write_text(body, encoding="utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        _FOUND_GROUPS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        arc = _FOUND_GROUPS_ARCHIVE_DIR / f"found_groups_{ts}.json"
        arc.write_text(body, encoding="utf-8")
    except OSError:
        return False
    return True


def _group_link_key(g: dict) -> str:
    """Ключ для дедупликации списков групп."""
    return str(g.get("link") or g.get("id") or "").strip().lower()


def _merge_group_lists(*lists: list[list[dict]]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for lst in lists:
        for g in lst:
            k = _group_link_key(g)
            if not k or "t.me" not in k:
                continue
            if k in seen:
                continue
            seen.add(k)
            out.append(g)
    return out


def _prompt_groups_list_source(action_title: str) -> list[dict] | None:
    """
    Выбор источника списка групп для вступления / сбора базы.
    None — отмена или ошибка.
    """
    s = Settings()
    found_path = Path("output") / "found_groups.json"
    gl_path = group_links_file_path(s)

    console.print()
    console.print(f"[bold]{escape(action_title)}[/] — [bold]откуда брать группы[/]")
    console.print(f"  [cyan]1[/]  [bold]found_groups.json[/] (результат поиска, п.1)")
    console.print(
        f"  [cyan]2[/]  [bold]{escape(str(gl_path))}[/] — txt, одна ссылка [dim]t.me[/] / [dim]telegram.me[/] на строку"
    )
    console.print("  [cyan]3[/]  Другой путь к .txt (те же правила)")
    console.print("  [cyan]4[/]  Объединить [bold]1[/] + [bold]2[/] (дубликаты ссылок убираются)")
    console.print("  [cyan]0[/]  Отмена")
    ch = Prompt.ask("Выбор", choices=["0", "1", "2", "3", "4"], default="1")

    if ch == "0":
        console.print("[dim]Отмена: список групп не выбран.[/]")
        return None

    if ch == "1":
        if not found_path.is_file():
            console.print("[red]Нет found_groups.json — выполните п.1 или используйте txt (п.2/3).[/]")
            return None
        try:
            data = json.loads(found_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            console.print("[red]found_groups.json повреждён (JSON).[/]")
            return None
        if not isinstance(data, list) or not data:
            console.print("[yellow]found_groups.json пуст.[/]")
            return None
        return data

    if ch == "2":
        groups = load_groups_from_links_txt(settings=s)
        if not groups:
            console.print(
                f"[red]Нет ссылок или нет файла. Создайте {escape(str(gl_path))} "
                f"(см. config/group_links.txt.example).[/]"
            )
            return None
        return groups

    if ch == "3":
        default_s = str(gl_path)
        raw = strip_c0_controls(Prompt.ask("Полный путь к .txt", default=default_s).strip())
        p = Path(raw).expanduser()
        if not p.is_file():
            console.print(f"[red]Файл не найден: {escape(str(p))}[/]")
            return None
        groups = load_groups_from_links_txt(path=p, settings=s)
        if not groups:
            console.print("[red]В файле нет строк со ссылками t.me / telegram.me[/]")
            return None
        return groups

    # ch == "4"
    a: list[dict] = []
    if found_path.is_file():
        try:
            data = json.loads(found_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                a = data
        except json.JSONDecodeError:
            pass
    b = load_groups_from_links_txt(settings=s)
    merged = _merge_group_lists(a, b)
    if not merged:
        console.print("[red]Нечего объединять: заполните found_groups.json и/или txt со ссылками.[/]")
        return None
    console.print(f"[dim]Объединено уникальных групп: {len(merged)}[/]")
    return merged


def _mk(key: str) -> str:
    """Клавиша пункта меню (цифра или буква): единый вид [key] в Rich."""
    return f"[[{key}]]"


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
    """Главное меню: пункты 1–9 по порядку, затем a; 0 — выход."""
    header = _load_header_art()
    try:
        console.print(Panel.fit(header, border_style="cyan"))
    except UnicodeEncodeError:
        console.print(Panel.fit(
            "[bold cyan]Vibe Marketing[/] - Telegram Lead Scraper",
            border_style="cyan",
        ))
    console.print()
    console.print("[bold white]── Данные ──[/]")
    console.print(f"{_mk('1')} Поиск групп")
    console.print(f"{_mk('2')} Просмотр найденных групп [dim](output/found_groups.json)[/]")
    console.print(f"{_mk('3')} Статистика базы")
    console.print(
        f"{_mk('4')} База пользователей [dim](SQLite)[/]: поиск, просмотр, экспорт уникальных username → txt"
    )
    console.print()
    console.print("[bold white]── Telegram ──[/]")
    console.print(
        f"{_mk('5')} Сбор базы пользователей [dim](подменю: один аккаунт или стандарт)[/]"
    )
    console.print(f"{_mk('6')} Вступить в группы")
    console.print(
        f"{_mk('7')} Добавить в контакты [dim](один аккаунт или пул)[/]"
    )
    console.print(f"{_mk('8')} Пригласить в канал")
    console.print()
    console.print("[bold white]── Система и сервис ──[/]")
    console.print(
        f"{_mk('9')} Импорты, настройки и аккаунты [dim](ZIP, рассылка БД/CSV, очистка, сессии, API)[/]"
    )
    console.print(
        f"{_mk('a')} Очистить список найденных групп [dim](found_groups.json, не БД)[/]"
    )
    console.print()
    console.print(f"{_mk('0')} Выход")
    console.print("[dim]Ввод: 1–9, a или 0.[/]")
    console.print()
    return Prompt.ask(
        "Выберите действие",
        choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a"],
        default="0",
    )


def _run_import_zip_interactive() -> None:
    """Импорт ZIP с парами .json + .session (хаб 9 → 1)."""
    console.print(
        "\n[bold]Импорт архива аккаунтов[/]\n"
        "[dim]В ZIP должны быть пары файлов с одним именем: name.json и name.session. "
        "api_id/api_hash можно не вводить сейчас — задайте telethon_default_api в «Настройках» "
        "или допишите в sidecar .json.[/]"
    )
    raw = strip_c0_controls(Prompt.ask("Полный путь к .zip", default="").strip())
    if not raw:
        console.print("[dim]Отмена.[/]")
        return
    zp = Path(raw).expanduser()
    if not zp.is_file():
        console.print(f"[red]Файл не найден: {escape(str(zp))}[/]")
        return
    mode = Prompt.ask(
        "Если файл с таким именем уже есть в папке сессий",
        choices=["skip", "overwrite"],
        default="skip",
    )
    try:
        with console.status("[bold]Импорт ZIP…[/]", spinner="dots"):
            rep = import_sessions_zip(zp, on_conflict=mode, settings=Settings())
    except (OSError, zipfile.BadZipFile) as e:
        console.print(f"[red]Ошибка импорта: {escape(str(e))}[/]")
        return
    except Exception as e:
        console.print(f"[red]Ошибка: {escape(str(e))}[/]")
        return
    print_zip_import_report(console, rep)
    Prompt.ask("\n[dim]Нажмите Enter…[/]", default="")


def _run_settings_submenu() -> None:
    """Настройки подключения: прокси, default api, синхронизация (хаб 9 → 2)."""
    while True:
        console.print()
        console.print("[bold white]── Настройки ──[/]")
        s = Settings()
        pe = "вкл" if is_proxy_enabled() else "выкл"
        ddir = str(telethon_session_dir_path(s)).replace("\\", "/")
        dapi = s.default_telethon_api_id
        dhash_ok = bool(s.default_telethon_api_hash)
        console.print(f"[dim]Прокси в рантайме:[/] [yellow]{pe}[/] · [dim]папка сессий:[/] [cyan]{ddir}[/]")
        console.print(
            f"[dim]telethon_default_api в settings:[/] "
            f"{'api_id=' + str(dapi) + ', api_hash задан' if dapi and dhash_ok else '[yellow]не задан[/]'}"
        )
        console.print(f"{_mk('1')} Включить / выключить использование прокси ([dim]proxy_enabled[/])")
        console.print(f"{_mk('2')} Назначить прокси аккаунтам (round-robin из пула → accounts.json)")
        console.print(f"{_mk('3')} Проверить прокси из пула")
        console.print(f"{_mk('4')} Задать telethon_default_api ([dim]api_id + api_hash для автопривязки сессий[/])")
        console.print(f"{_mk('5')} Синхронизировать папку сессий → accounts.json [dim](как при старте)[/]")
        console.print(
            f"{_mk('6')} Удалить ключи API [dim](api_id/api_hash из accounts.json + sidecar; перед git push)[/]"
        )
        console.print(f"{_mk('0')} Назад")
        console.print()
        sub = Prompt.ask("Выбор", choices=["0", "1", "2", "3", "4", "5", "6"], default="0")
        if sub == "0":
            break
        try:
            if sub == "1":
                cur = is_proxy_enabled()
                turn = Confirm.ask(
                    f"Прокси сейчас [bold]{'включены' if cur else 'выключены'}[/]. Переключить?",
                    default=not cur,
                )
                ok, msg = set_proxy_enabled(turn)
                console.print(f"[green]{msg}[/]" if ok else f"[red]{msg}[/]")
            elif sub == "2":
                _run_assign_proxies()
            elif sub == "3":
                asyncio.run(_run_check_proxies())
            elif sub == "4":
                console.print(
                    "[dim]Один api_id/api_hash на все новые .session без своих ключей в sidecar. "
                    "Можно пропустить и задать позже.[/]"
                )
                if not Confirm.ask("Записать telethon_default_api в settings.json?", default=True):
                    continue
                aid_s = strip_c0_controls(Prompt.ask("api_id").strip())
                aid = parse_api_id_digits(aid_s)
                if aid is None:
                    console.print("[red]Некорректный api_id[/]")
                    continue
                ah = strip_c0_controls(Prompt.ask("api_hash").strip())
                if not ah:
                    console.print("[red]Пустой api_hash[/]")
                    continue
                ok, msg = set_telethon_default_api(aid, ah)
                console.print(f"[green]Сохранено:[/] {msg}" if ok else f"[red]{msg}[/]")
            elif sub == "5":
                with console.status("[bold]Синхронизация сессий…[/]", spinner="dots"):
                    n, warns = sync_sessions_dir_to_accounts(Settings())
                console.print(f"[green]Добавлено/обновлено записей в accounts.json:[/] {n}")
                for w in warns[:12]:
                    console.print(f"  [yellow]{escape(str(w))}[/]")
                if len(warns) > 12:
                    console.print(f"  [dim]… ещё {len(warns) - 12}[/]")
            elif sub == "6":
                console.print(
                    "[yellow]Снимаются api_id/api_hash[/] со всех записей с [cyan]session_name[/] "
                    "в [cyan]accounts.json[/] и из [cyan]sessions/*.json[/] (прокси и .session не трогаются). "
                    "Без ключей Telethon не запустится, пока не назначите API снова ([bold]9→6[/] из пакета или [bold]9→2→4[/])."
                )
                if not Confirm.ask("Продолжить?", default=False):
                    continue
                n_acc, n_side, path = strip_api_credentials_from_accounts(Settings())
                console.print(
                    f"[green]Готово:[/] записей в accounts.json без ключей: {n_acc}, "
                    f"обновлено sidecar: {n_side} · [dim]{escape(path)}[/]"
                )
                if Confirm.ask(
                    "Также обнулить telethon_default_api в settings.json?",
                    default=False,
                ):
                    ok_d, msg_d = clear_telethon_default_api()
                    console.print(f"[green]{escape(msg_d)}[/]" if ok_d else f"[red]{escape(msg_d)}[/]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Прервано.[/]")
        except Exception as e:
            console.print(f"[red]Ошибка: {escape(str(e))}[/]")
        Prompt.ask("\n[dim]Enter — продолжить[/]", default="")


def _run_mytelegram_api_placeholder() -> None:
    """Playwright: Telegram Web → пауза → my.telegram.org → запись api_id/api_hash."""
    from src.mytelegram_portal.runner import (
        collect_jobs_from_accounts,
        collect_jobs_from_session_files,
        run_mytg_menu_flow,
    )

    console.print()
    console.print("[bold white]── API my.telegram.org (Playwright) ──[/]")
    console.print(
        "[dim]Фаза 1:[/] вход в [cyan]web.telegram.org/k[/] по номеру, код **в консоль** → "
        "[cyan]output/mytg_web_storage/[/]. "
        "[dim]Фаза 2:[/] [cyan]my.telegram.org[/] → [cyan]accounts.json[/] + sidecar. "
        "[bold]mytg_*[/] в settings.json; сервер: [dim]xvfb-run[/] + [bold]mytg_headless: false[/]."
    )
    console.print(
        "[dim]Источник A —[/] [bold]accounts.json[/]: session_name + phone + proxy. "
        "[dim]Источник B —[/] папка [bold]*.session[/]: имя = 8–15 цифр номера [dim](можно с + в начале)[/], "
        "прокси round-robin из [cyan]config/proxies.txt[/]."
    )
    console.print(f"{_mk('0')} Назад")
    console.print(f"{_mk('1')} Фаза 1 [dim](accounts.json)[/]")
    console.print(f"{_mk('2')} Фаза 2 [dim](accounts.json)[/]")
    console.print(f"{_mk('3')} Полный [dim](accounts.json)[/]")
    console.print(f"{_mk('4')} Фаза 1 [dim](*.session + proxies.txt)[/]")
    console.print(f"{_mk('5')} Фаза 2 [dim](*.session + proxies.txt)[/]")
    console.print(f"{_mk('6')} Полный [dim](*.session + proxies.txt)[/]")
    sub = Prompt.ask("Выбор", choices=["0", "1", "2", "3", "4", "5", "6"], default="0")
    if sub == "0":
        return
    mode_map = {
        "1": ("phase1", False),
        "2": ("phase2", False),
        "3": ("full", False),
        "4": ("phase1", True),
        "5": ("phase2", True),
        "6": ("full", True),
    }
    mode, from_sess = mode_map[sub]
    sett = Settings()
    if from_sess:
        jobs_preview = collect_jobs_from_session_files(sett, console)
    else:
        jobs_preview = collect_jobs_from_accounts(console, _PROJECT_ROOT)
    jobs_override_mytg = None
    if jobs_preview:
        console.print("[dim]Задачи (нумерация = порядок прогона):[/]")
        cap = 40
        for i, job in enumerate(jobs_preview[:cap], 1):
            row = account_row_for_session_name(job.session_name) or {}
            hint = format_last_use_line(row)
            extra = (
                f" [dim]{escape(hint)}[/]"
                if hint and hint != "—"
                else ""
            )
            console.print(f"  [cyan]{i}[/]  {escape(job.session_name)}{extra}")
        if len(jobs_preview) > cap:
            console.print(f"  [dim]… ещё {len(jobs_preview) - cap}[/]")
        idx_set = prompt_row_indices_scope(
            console, len(jobs_preview), title="Задачи my.telegram.org / Web"
        )
        jobs_override_mytg = [jobs_preview[j - 1] for j in sorted(idx_set)]
    if jobs_preview:
        console.print("[dim]Запуск:[/]  [cyan]1[/] в этой консоли  [cyan]2[/] в фоне")
        if Prompt.ask("Режим", choices=["1", "2"], default="1") == "2":
            payload = {
                "version": 1,
                "task": "mytg",
                "payload": {
                    "mode": mode,
                    "from_session_files": from_sess,
                    "jobs_override": [j.to_json() for j in (jobs_override_mytg or [])],
                },
            }
            jid, _, logp = spawn_worker_job(
                payload,
                task_label="mytg",
                summary=f"{mode} n={len(jobs_override_mytg or [])}",
            )
            console.print(
                f"[green]Фон:[/] id [cyan]{jid}[/] · лог [cyan]{logp}[/] · "
                f"[dim]реестр output/job_registry.jsonl[/]"
            )
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return
    try:
        run_mytg_menu_flow(
            console,
            settings=sett,
            mode=mode,  # type: ignore[arg-type]
            from_session_files=from_sess,
            jobs_override=jobs_override_mytg,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Прервано.[/]")
    Prompt.ask("\n[dim]Enter — назад[/]", default="")


def _run_broadcast_from_bundle_menu() -> None:
    """Рассылка ЛС из пакета: zip, proxies.txt, text_1/2.txt, 1–3.jpg."""
    console.print()
    console.print("[bold white]── Рассылка из пакета ──[/]")
    console.print(
        "[dim]В одном каталоге-пакете:[/] [cyan]accounts.zip[/], [cyan]apis.txt[/], "
        "[cyan]proxy.txt[/] [dim](или[/] [cyan]proxies.txt[/][dim])[/], тексты и jpg; опционально "
        "[cyan]sessions_bind.txt[/], [cyan]apis_sessions.txt[/]; "
        "[dim]рядом[/] [cyan]accounts2.zip[/] + [cyan]apis2.txt[/] + [cyan]proxy2.txt[/] …"
    )
    sett = Settings()
    default_dir = _PROJECT_ROOT / sett.campaign_dir
    raw = strip_c0_controls(
        Prompt.ask("Каталог пакета", default=str(default_dir)).strip()
    )
    root = Path(raw).expanduser()
    bundle = load_campaign_bundle(root)
    send_media = Confirm.ask("Прикладывать фото к сообщениям (1.jpg–3.jpg)?", default=True)
    slices = discover_campaign_import_slices(root)
    errs = validate_campaign_bundle(bundle, require_images=send_media)
    errs.extend(validate_extra_import_slices(slices))
    if errs:
        console.print("[red]Пакет не готов:[/]")
        for e in errs:
            console.print(f"  [red]{escape(e)}[/]")
        Prompt.ask("\n[dim]Enter — назад[/]", default="")
        return
    console.print("[green]Пакет проверен.[/]")

    stems_by_label: dict[str, frozenset[str]] = {}
    stem_to_apis_file: dict[str, Path] = {}
    stem_to_proxy_file: dict[str, Path] = {}
    unique_import_order: list[str] = []

    zip_conflict = "skip"
    if Confirm.ask("Импортировать ZIP сессий (все accounts.zip, accounts2.zip…) в каталог сессий?", default=True):
        zip_conflict = (
            "overwrite"
            if Confirm.ask("Перезаписать совпадающие файлы на диске?", default=False)
            else "skip"
        )
        for sl in slices:
            if not sl.zip_path.is_file():
                console.print(f"[dim]Нет {escape(sl.zip_path.name)} — пропуск.[/]")
                stems_by_label[sl.label] = frozenset()
                continue
            try:
                rep = import_sessions_zip(sl.zip_path, on_conflict=zip_conflict, settings=sett)
                console.print(f"[bold]{escape(sl.label)}[/] — {escape(sl.zip_path.name)}")
                print_zip_import_report(console, rep)
                stems_by_label[sl.label] = frozenset(rep.imported_stems)
            except (OSError, zipfile.BadZipFile) as e:
                console.print(f"[red]Ошибка ZIP {escape(sl.zip_path.name)}: {escape(str(e))}[/]")
                Prompt.ask("\n[dim]Enter — назад[/]", default="")
                return
    else:
        for sl in slices:
            stems_by_label[sl.label] = frozenset()

    seen_stem: set[str] = set()
    for sl in slices:
        for st in stems_by_label.get(sl.label, frozenset()):
            stem_to_apis_file[st] = sl.apis_file
            stem_to_proxy_file[st] = sl.proxies_file
            if st not in seen_stem:
                seen_stem.add(st)
                unique_import_order.append(st)

    for sl in slices:
        if not sl.zip_path.is_file():
            continue
        api_pairs = load_api_pairs_from_file(sl.apis_file)
        if not api_pairs:
            console.print(f"[red]В {escape(sl.apis_file.name)} нет валидных пар api_id:api_hash.[/]")
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return
        proxy_lines = load_proxy_pool_from_file(sl.proxies_file)
        if not proxy_lines:
            console.print(
                f"[red]В {escape(sl.proxies_file.name)} нет валидных строк прокси "
                f"(или создайте proxy.txt / proxies.txt).[/]"
            )
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return

    if Confirm.ask(
        "Назначить API и прокси (sessions_bind → apis_sessions → RR apis.txt / proxy.txt)?",
        default=True,
    ):
        if unique_import_order:
            if not apply_package_api_proxy_assignments(
                console=console,
                sett=sett,
                root=root,
                unique_imported=unique_import_order,
                stem_to_apis_file=stem_to_apis_file,
                stem_to_proxy_file=stem_to_proxy_file,
            ):
                Prompt.ask("\n[dim]Enter — назад[/]", default="")
                return
        else:
            legacy_ok = False
            for sl in slices:
                if not sl.zip_path.is_file() or sl.label != "accounts":
                    continue
                api_pairs = load_api_pairs_from_file(sl.apis_file)
                proxy_lines = load_proxy_pool_from_file(sl.proxies_file)
                ok_api, msg_api = assign_apis_round_robin_to_accounts(api_pairs, sett)
                if not ok_api:
                    console.print(f"[red]{escape(msg_api)}[/]")
                    Prompt.ask("\n[dim]Enter — назад[/]", default="")
                    return
                console.print(f"[green]API ({escape(sl.label)}):[/] [dim]{escape(msg_api)}[/]")
                ok, msg = assign_proxies_round_robin_to_accounts(sett, proxy_pool=proxy_lines)
                if not ok:
                    console.print(f"[red]{escape(msg)}[/]")
                    Prompt.ask("\n[dim]Enter — назад[/]", default="")
                    return
                console.print(f"[green]Прокси ({escape(sl.label)}):[/] [dim]{escape(msg)}[/]")
                legacy_ok = True
                break
            if not legacy_ok:
                console.print(
                    "[yellow]Нет импортированных стемов и нет accounts.zip — "
                    "назначьте API/прокси вручную или импортируйте ZIP.[/]"
                )

    combined_api: list[tuple[int, str]] = []
    for sl in slices:
        if sl.zip_path.is_file():
            combined_api.extend(load_api_pairs_from_file(sl.apis_file))
    if combined_api:
        ok_fill, _msg_fill, n_fill = assign_apis_fill_missing_in_accounts(combined_api, sett)
        if ok_fill and n_fill:
            console.print(
                f"[green]API из пакета дописан[/] [dim]аккаунтам без api_id/api_hash:[/] "
                f"[white]{n_fill}[/] [dim](часто стем = цифры номера после отдельного входа — "
                f"раньше API по ZIP не назначался таким строкам)[/]"
            )
        elif not ok_fill:
            console.print(f"[yellow]{escape(_msg_fill)}[/]")

    console.print()
    console.print("[bold white]── Аккаунты для рассылки ──[/]")
    console.print(
        f"{_mk('1')} Все аккаунты из accounts.json [dim](параллельно, обычный режим)[/]"
    )
    console.print(
        f"{_mk('2')} Один аккаунт из списка — [bold]без прокси[/] [dim](только эта сессия; как «общий» в п.5)[/]"
    )
    console.print(
        f"{_mk('3')} Отдельный вход [dim](телефон, код; API — [bold]случайная пара из apis.txt[/] пакета; "
        f"прокси — URL или host:port:user:pass)[/]"
    )
    acc_mode = Prompt.ask("Режим аккаунтов", choices=["1", "2", "3"], default="1")

    broadcast_extra_kw: dict = {}
    broadcast_package_api_pair: tuple[int, str] | None = None

    if acc_mode == "1":
        if not load_accounts():
            console.print("[red]Нет аккаунтов в accounts.json после импорта.[/]")
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return
        tcat = build_telethon_catalog()
        if not tcat:
            console.print("[red]Нет аккаунтов с api_id+api_hash.[/]")
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return
        print_account_catalog_table(console, tcat)
        _, scope = prompt_account_scope(console, tcat, title="Рассылка: аккаунты")
        if not scope:
            console.print("[red]Пустой набор аккаунтов.[/]")
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return
        broadcast_extra_kw["only_session_names"] = scope
        console.print(f"[dim]В прогоне аккаунтов:[/] [cyan]{len(scope)}[/]")
    elif acc_mode == "2":
        tcat = build_telethon_catalog()
        if not tcat:
            console.print(
                "[red]Нет аккаунтов в accounts.json.[/] Добавьте сессию: главное меню → [bold]9[/] → [bold]3[/]."
            )
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return
        print_account_catalog_table(console, tcat)
        pick = _prompt_nonneg_int(
            "id аккаунта из таблицы (один, без прокси в этом прогоне)",
            default=1,
            minimum=1,
            maximum=len(tcat),
        )
        sn_one = tcat[pick - 1].session_name.strip()
        if not sn_one:
            console.print("[red]У записи нет session_name.[/]")
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return
        broadcast_extra_kw["only_session_names"] = frozenset([sn_one])
        broadcast_extra_kw["no_proxy"] = True
        console.print(
            "[dim]Рассылка только с выбранного аккаунта; прокси для этого прогона отключены.[/]"
        )
    elif acc_mode == "3":
        api_pool: list[tuple[int, str]] = []
        for sl in slices:
            if sl.zip_path.is_file():
                api_pool.extend(load_api_pairs_from_file(sl.apis_file))
        if not api_pool:
            console.print(
                "[red]Нет пар api_id:api_hash для отдельного входа — проверьте apis.txt у слайсов с ZIP.[/]"
            )
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return
        broadcast_package_api_pair = random.choice(api_pool)
        console.print(
            f"[dim]Для входа выбрана случайная пара из пакета: [bold]api_id={broadcast_package_api_pair[0]}[/] "
            f"[dim](hash скрыт). Дальше — телефон, код, опционально прокси.[/]"
        )

    cat_key = Prompt.ask("Категория базы", choices=["hot", "warm", "all"], default="hot")
    cat_val = None if cat_key == "all" else cat_key
    limit = _prompt_nonneg_int(
        "Сколько пользователей из БД (макс. в выборке)",
        default=200,
        minimum=1,
        maximum=100_000,
    )
    ex_inv = Confirm.ask(
        "Исключить строк с invited_to_channel_at [dim](как в других пунктах)[/]?",
        default=True,
    )

    console.print()
    console.print(
        f"{_mk('1')} Обычная рассылка [dim](без отмеченных privacy-очередью)[/]"
    )
    console.print(
        f"{_mk('2')} Повтор [dim](только очередь после UserPrivacyRestricted; см. п.6 базы / п.4 контакты)[/]"
    )
    bmode = Prompt.ask("Режим рассылки", choices=["1", "2"], default="1")
    broadcast_mode = "privacy_retry" if bmode == "2" else "normal"

    console.print(
        "[yellow]Массовая рассылка незнакомцам нарушает правила Telegram, может привести к бану аккаунтов "
        "и юридическим претензиям. Используйте только с согласия получателей.[/]"
    )
    if not Confirm.ask("Начать рассылку?", default=False):
        return

    if acc_mode in ("1", "2"):
        console.print("[dim]Запуск:[/]  [cyan]1[/] в этой консоли  [cyan]2[/] в фоне")
        if Prompt.ask("Режим", choices=["1", "2"], default="1") == "2":
            only_list = list(broadcast_extra_kw.get("only_session_names") or [])
            job_payload = {
                "version": 1,
                "task": "broadcast_bundle",
                "payload": {
                    "campaign_dir": str(root.resolve()),
                    "limit": limit,
                    "category": cat_key,
                    "zip_conflict": zip_conflict,
                    "broadcast_mode": broadcast_mode,
                    "send_media": send_media,
                    "exclude_invited": ex_inv,
                    "only_session_names": only_list,
                    "broadcast_delay_minutes": None,
                    "broadcast_account_gap_minutes": None,
                },
            }
            jid, _, logp = spawn_worker_job(
                job_payload,
                task_label="broadcast",
                summary=f"{root.name} limit={limit}",
            )
            console.print(
                f"[green]Фон:[/] id [cyan]{jid}[/] · лог [cyan]{logp}[/] · "
                f"[dim]реестр output/job_registry.jsonl[/]"
            )
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return

    async def _go_async():
        db = get_db()
        if acc_mode == "3":
            logged = await login_client_for_one_off_scrape(
                console,
                package_api_pair=broadcast_package_api_pair,
            )
            if not logged:
                return None, None
            client, meta = logged
            try:
                totals = await run_dm_broadcast(
                    bundle=bundle,
                    db=db,
                    settings=sett,
                    console=console,
                    category=cat_val,
                    total_limit=limit,
                    exclude_invited=ex_inv,
                    broadcast_mode=broadcast_mode,
                    send_media=send_media,
                    fixed_client=client,
                    fixed_session_label=str(meta.get("session_name") or ""),
                    disconnect_fixed_client=False,
                )
                return totals, meta
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass
        totals = await run_dm_broadcast(
            bundle=bundle,
            db=db,
            settings=sett,
            console=console,
            category=cat_val,
            total_limit=limit,
            exclude_invited=ex_inv,
            broadcast_mode=broadcast_mode,
            send_media=send_media,
            **broadcast_extra_kw,
        )
        return totals, None

    try:
        out = asyncio.run(_go_async())
    except KeyboardInterrupt:
        console.print("\n[yellow]Прервано.[/]")
        Prompt.ask("\n[dim]Enter — назад[/]", default="")
        return

    if out is None:
        return
    totals, meta_after = out

    if acc_mode == "3" and meta_after and Confirm.ask(
        "Добавить этот аккаунт в accounts.json?", default=False
    ):
        upsert_telethon_account(
            meta_after["session_name"],
            meta_after["api_id"],
            meta_after["api_hash"],
            phone=meta_after.get("phone"),
            proxy=meta_after.get("proxy_url"),
        )
        console.print(f"[green]Записано в {accounts_json_path()}[/]")

    console.print()
    un_nf = totals.username_not_found_marked
    un_part = (
        f", нет @username в TG [dim](пометка в БД)[/] [white]{un_nf}[/]"
        if un_nf
        else ""
    )
    console.print(
        f"[bold green]Итого:[/] отправлено [white]{totals.sent}[/], "
        f"ошибок [red]{totals.failed}[/], пропусков [yellow]{totals.skipped}[/], "
        f"privacy [magenta]{totals.privacy_skipped}[/], лимит/день [cyan]{totals.deferred_daily_cap}[/], "
        f"relay [red]{totals.relay_exhausted}[/]{un_part}"
    )
    for sn, row in sorted(totals.by_session.items(), key=lambda x: x[0]):
        s, f, sk, pr, dc, rx = row
        if s or f or sk or pr or dc or rx:
            console.print(
                f"  [dim]{escape(str(sn))}:[/] ok={s} fail={f} skip={sk} privacy={pr} "
                f"daily_cap={dc} relay={rx}"
            )
    if totals.by_session:
        n_touch = touch_accounts_last_use(
            list(totals.by_session.keys()), kind="broadcast"
        )
        if n_touch:
            console.print(
                f"[dim]В accounts.json обновлено[/] [cyan]last_broadcast_at[/]: "
                f"[white]{n_touch}[/] [dim]записей[/]"
            )
    Prompt.ask("\n[dim]Enter — назад[/]", default="")


def _run_csv_broadcast_interactive_menu() -> None:
    """Рассылка по CSV из пакета — те же параметры, что у --csv-broadcast / --csv-recipients."""
    console.print()
    console.print("[bold white]── Рассылка по CSV из пакета ──[/]")
    console.print(
        "[dim]Как в CLI:[/] [cyan]--csv-broadcast[/] [dim]+[/] [cyan]--csv-recipients[/][dim];[/] "
        "в каталоге — ZIP, apis, proxy, [cyan]text_1/2.txt[/]; при медиа — [cyan]1.jpg–3.jpg[/]. "
        "В CSV нужны колонки [bold]User ID[/] и [bold]Username[/]."
    )
    sett = Settings()
    default_dir = _PROJECT_ROOT / sett.campaign_dir
    raw_dir = strip_c0_controls(
        Prompt.ask("Каталог пакета (campaign)", default=str(default_dir)).strip()
    )
    csv_raw = strip_c0_controls(Prompt.ask("Путь к CSV получателей").strip())
    if not csv_raw:
        console.print("[red]Нужен путь к CSV.[/]")
        Prompt.ask("\n[dim]Enter — назад[/]", default="")
        return

    delay_spec = strip_c0_controls(
        Prompt.ask(
            "Пауза сессии между ЛС, минуты [dim](число или MIN-MAX, напр. 20-30)[/]",
            default="20-30",
        ).strip()
    ) or "30"

    gap_raw = strip_c0_controls(
        Prompt.ask(
            "Зазор между аккаунтами одной API-группы, мин [dim](MIN-MAX или число; Enter — из settings)[/]",
            default="",
        ).strip()
    )
    gap_spec = gap_raw if gap_raw else None

    send_media = Confirm.ask("Прикладывать изображения 1.jpg–3.jpg?", default=True)

    zip_conflict = (
        "overwrite"
        if Confirm.ask(
            "При импорте ZIP перезаписывать совпадающие файлы сессий на диске?",
            default=False,
        )
        else "skip"
    )

    limit_raw = strip_c0_controls(
        Prompt.ask("Макс. строк из CSV [dim](Enter — без лимита)[/]", default="").strip()
    )
    csv_limit: int | None = int(limit_raw) if limit_raw.isdigit() else None

    log_raw = strip_c0_controls(
        Prompt.ask(
            "JSONL лог успешных отправок [dim](Enter — output/csv_broadcast_sent.jsonl)[/]",
            default="",
        ).strip()
    )
    sent_log = log_raw if log_raw else None

    skip_sent = Confirm.ask(
        "Пропускать user_id, уже записанные в этом JSONL?", default=False
    )

    console.print()
    console.print("[dim]Сводка:[/]")
    console.print(f"  пакет: [cyan]{escape(raw_dir)}[/]")
    console.print(f"  CSV:   [cyan]{escape(csv_raw)}[/]")
    console.print(f"  пауза сессии: [cyan]{escape(delay_spec)}[/] мин")
    console.print(
        "  зазор API-группы: "
        f"[cyan]{escape(gap_raw) if gap_raw else 'из settings.json'}[/]"
    )
    console.print(f"  медиа: [cyan]{'да' if send_media else 'нет (только текст)'}[/]")
    console.print(f"  ZIP при конфликте: [cyan]{escape(zip_conflict)}[/]")
    if csv_limit is not None:
        console.print(f"  лимит CSV: [cyan]{csv_limit}[/]")
    if sent_log:
        console.print(f"  лог: [cyan]{escape(sent_log)}[/]")
    if skip_sent:
        console.print("  пропуск по логу: [cyan]да[/]")

    if not Confirm.ask("Запустить?", default=False):
        Prompt.ask("\n[dim]Enter — назад[/]", default="")
        return

    try:
        code = run_csv_broadcast_cli(
            raw_dir,
            csv_raw,
            delay_spec,
            zip_conflict,
            csv_limit=csv_limit,
            send_media=send_media,
            sent_log=sent_log,
            skip_sent=skip_sent,
            csv_account_gap_spec=gap_spec,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Прервано.[/]")
        Prompt.ask("\n[dim]Enter — назад[/]", default="")
        return
    except Exception as e:
        console.print(f"[red]{escape(str(e))}[/]")
        Prompt.ask("\n[dim]Enter — назад[/]", default="")
        return

    if code != 0:
        console.print(f"[red]Код выхода:[/] [white]{code}[/]")
    Prompt.ask("\n[dim]Enter — назад[/]", default="")


def _run_cleanup_accounts_menu() -> None:
    """Очистка accounts.json, сессий на диске, пула прокси."""
    console.print()
    console.print("[bold white]── Очистка аккаунтов и прокси ──[/]")
    sess_dir = telethon_session_dir_path(Settings())
    console.print(f"{_mk('1')} Очистить только [bold]accounts.json[/] [dim](пустой список)[/]")
    console.print(
        f"{_mk('2')} [bold]accounts.json[/] + удалить [bold]*.session[/] и sidecar [bold]*.json[/] в "
        f"[dim]{escape(str(sess_dir))}[/]"
    )
    console.print(f"{_mk('3')} Очистить [bold]config/proxies.txt[/] и [bold]proxies.list[/] в settings.json")
    console.print(f"{_mk('4')} Полный сброс [dim](всё выше без дополнительных вопросов)[/]")
    console.print(f"{_mk('0')} Отмена")
    sub = Prompt.ask("Выбор", choices=["0", "1", "2", "3", "4"], default="0")
    if sub == "0":
        return

    if sub == "4":
        if not Confirm.ask(
            "Полный сброс: accounts.json, все сессии в каталоге, прокси. Продолжить?",
            default=False,
        ):
            return
        clear_accounts_json()
        console.print("[green]accounts.json очищен.[/]")
        n = wipe_telethon_session_files(Settings())
        console.print(f"[green]Удалено *.session:[/] {n}")
        ok, msg = clear_proxy_pool_in_config()
        console.print(f"[green]Прокси очищены:[/] [dim]{escape(msg)}[/]" if ok else f"[red]{escape(msg)}[/]")
        Prompt.ask("\n[dim]Enter — назад[/]", default="")
        return

    if sub == "1":
        if not Confirm.ask("Обнулить accounts.json?", default=False):
            return
        clear_accounts_json()
        console.print("[green]accounts.json очищен.[/]")
    elif sub == "2":
        if not Confirm.ask("Обнулить accounts.json?", default=False):
            return
        clear_accounts_json()
        console.print("[green]accounts.json очищен.[/]")
        if not Confirm.ask(f"Удалить все *.session в {sess_dir}?", default=False):
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return
        n = wipe_telethon_session_files(Settings())
        console.print(f"[green]Удалено *.session:[/] {n}")
    elif sub == "3":
        if not Confirm.ask("Очистить proxies.txt и proxies.list в settings?", default=False):
            return
        ok, msg = clear_proxy_pool_in_config()
        console.print(f"[green]Прокси очищены:[/] [dim]{escape(msg)}[/]" if ok else f"[red]{escape(msg)}[/]")

    Prompt.ask("\n[dim]Enter — назад[/]", default="")


def _run_bulk_prepare_menu() -> None:
    """Подготовка аккаунтов: выбор аккаунтов + консоль или фон."""
    cat = build_telethon_catalog()
    if not cat:
        console.print("[red]Нет аккаунтов с api_id+api_hash в accounts.json.[/]")
        Prompt.ask("\n[dim]Enter — назад[/]", default="")
        return
    print_account_catalog_table(console, cat)
    _, scope = prompt_account_scope(console, cat, title="Подготовка аккаунтов (2FA, прокси, сброс)")
    if not scope:
        console.print("[red]Пустой набор аккаунтов.[/]")
        Prompt.ask("\n[dim]Enter — назад[/]", default="")
        return
    console.print("[dim]Запуск:[/]  [cyan]1[/] в этой консоли  [cyan]2[/] в фоне")
    if Prompt.ask("Режим", choices=["1", "2"], default="1") == "2":
        s = Settings()
        pwd_plain = s.bulk_2fa_password
        if not pwd_plain:
            console.print(
                "[yellow]Пароль возьмётся из ввода (в job-файле на диске); "
                "после выполнения можно удалить output/job_payloads/*.json[/]"
            )
            pwd_plain = Prompt.ask("Пароль облачного 2FA", password=True)
        if not pwd_plain:
            console.print("[red]Без пароля фоновая задача не запускается.[/]")
            Prompt.ask("\n[dim]Enter — назад[/]", default="")
            return
        payload = {
            "version": 1,
            "task": "bulk_prepare",
            "payload": {
                "only_session_names": sorted(scope),
                "password_plain": pwd_plain,
            },
        }
        jid, _, logp = spawn_worker_job(
            payload,
            task_label="bulk_prepare",
            summary=f"n={len(scope)}",
        )
        console.print(
            f"[green]Фон:[/] id [cyan]{jid}[/] · лог [cyan]{logp}[/]"
        )
        Prompt.ask("\n[dim]Enter — назад[/]", default="")
        return
    try:
        asyncio.run(
            run_bulk_account_prepare(console, only_session_names=scope)
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Прервано.[/]")
    Prompt.ask("\n[dim]Enter — назад[/]", default="")


def _run_accounts_catalog_menu() -> None:
    """Таблица аккаунтов с id для выбора в других пунктах."""
    console.print()
    console.print("[bold white]── Сводка аккаунтов (Telethon) ──[/]")
    cat = build_telethon_catalog()
    if not cat:
        console.print("[yellow]Нет аккаунтов с api_id+api_hash в accounts.json.[/]")
    else:
        print_account_catalog_table(console, cat)
    console.print(
        "[dim]Колонка[/] [cyan]id[/][dim]: те же номера, что в запросах «выбрать по id» "
        "(например [cyan]1,3,5[/]). См. рассылку, mytg, вступления, сбор базы.[/]"
    )
    Prompt.ask("\n[dim]Enter — назад[/]", default="")


def _run_background_jobs_menu() -> None:
    """Последние фоновые задачи и пути к логам."""
    console.print()
    console.print("[bold white]── Фоновые задачи ──[/]")
    rows = read_recent_registry_entries(limit=25)
    if not rows:
        console.print("[dim]Пока пусто. Реестр:[/] [cyan]output/job_registry.jsonl[/]")
    else:
        for r in rows:
            jid = r.get("job_id", "?")
            task = r.get("task", "?")
            st = r.get("status", "?")
            pid = r.get("pid") or 0
            logp = r.get("log_path", "")
            try:
                p_int = int(pid)
            except (TypeError, ValueError):
                p_int = 0
            alive = pid_alive(p_int) if p_int else False
            extra = " · pid активен" if alive else ""
            console.print(
                f"  [cyan]{escape(str(jid))}[/]  {escape(str(task))}  "
                f"[dim]{escape(str(st))}[/]{extra}"
            )
            if logp:
                console.print(f"    [dim]лог:[/] {escape(str(logp))}")
    console.print("[dim]Каталог логов:[/] [cyan]output/job_logs/[/]")
    Prompt.ask("\n[dim]Enter — назад[/]", default="")


def _run_system_hub_submenu() -> None:
    """Хаб: импорт, настройки, сессии, опционально API."""
    while True:
        console.print()
        console.print("[bold white]── Импорты, настройки и аккаунты ──[/]")
        console.print(f"{_mk('1')} Импорт ZIP [dim](пары .json + .session)[/]")
        console.print(f"{_mk('2')} Настройки [dim](прокси, telethon_default_api, синхронизация)[/]")
        console.print(f"{_mk('3')} Сессии Telethon [dim](список, привязка, вход, автопривязка)[/]")
        console.print(f"{_mk('4')} API my.telegram.org [dim](опционально)[/]")
        console.print(f"{_mk('5')} Подготовка аккаунтов [dim](2FA, прокси, сброс сессий)[/]")
        console.print(f"{_mk('6')} Рассылка из пакета [dim](ZIP, прокси, тексты, опц. фото, БД)[/]")
        console.print(
            f"{_mk('7')} Рассылка по CSV [dim](пакет + recipients.csv, паузы как --csv-broadcast)[/]"
        )
        console.print(f"{_mk('8')} Очистка аккаунтов и прокси [dim](быстрый сброс)[/]")
        console.print(f"{_mk('9')} Сводка аккаунтов [dim](таблица id / session / последнее использование)[/]")
        console.print(f"{_mk('10')} Фоновые задачи [dim](логи, реестр)[/]")
        console.print(f"{_mk('0')} Назад в главное меню")
        console.print()
        sub = Prompt.ask(
            "Выбор",
            choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
            default="0",
        )
        if sub == "0":
            break
        try:
            if sub == "1":
                _run_import_zip_interactive()
            elif sub == "2":
                _run_settings_submenu()
            elif sub == "3":
                asyncio.run(run_telethon_session_menu(console))
            elif sub == "4":
                _run_mytelegram_api_placeholder()
            elif sub == "5":
                _run_bulk_prepare_menu()
            elif sub == "6":
                _run_broadcast_from_bundle_menu()
            elif sub == "7":
                _run_csv_broadcast_interactive_menu()
            elif sub == "8":
                _run_cleanup_accounts_menu()
            elif sub == "9":
                _run_accounts_catalog_menu()
            elif sub == "10":
                _run_background_jobs_menu()
        except KeyboardInterrupt:
            console.print("\n[yellow]Прервано.[/]")
        except Exception as e:
            console.print(f"[red]Ошибка: {escape(str(e))}[/]")


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
    console.print(
        "[dim]Запросы: темы из keywords × города из [bold]data/cities_by.json[/]. "
        "Города РФ из [bold]data/russian_cities_blocklist.json[/] не участвуют в запросах и отсекаются в выдаче "
        f"([bold]exclude_russian_cities_in_search[/]: {s.exclude_russian_cities_in_search}).[/]"
    )
    console.print()

    progress_state = {
        "source": "",
        "query": "",
        "cur": 0,
        "total": 1,
        "found": 0,
        "proxy": "",
        "worker_note": "",
    }
    live_ref: list = []

    def make_panel() -> Panel:
        src = progress_state["source"]
        q = progress_state["query"]
        cur = progress_state["cur"]
        tot = progress_state["total"]
        found = progress_state["found"]
        proxy = progress_state["proxy"]
        note = progress_state.get("worker_note") or ""
        pct = (cur / tot * 100) if tot else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        proxy_line = f"[dim]Прокси:[/] [yellow]{escape(str(proxy))}[/]\n" if proxy else ""
        note_line = f"[dim]{escape(note)}[/]\n" if note else ""
        return Panel(
            f"[cyan]{escape(str(src))}[/]\n"
            f"[dim]Запрос:[/] {escape(q[:60])}{'...' if len(q) > 60 else ''}\n"
            f"{proxy_line}"
            f"{note_line}"
            f"[green][{bar}][/] {cur}/{tot} ({pct:.0f}%)\n"
            f"[bold]Найдено групп:[/] [green]{found}[/]",
            title="[bold]Поиск[/]",
            border_style="blue",
        )

    def on_progress(
        source: str,
        query: str,
        cur: int,
        total: int,
        found: int,
        proxy_info: str = "",
        worker_note: str = "",
    ) -> None:
        progress_state.update(
            source=source,
            query=query,
            cur=cur,
            total=total,
            found=found,
            proxy=proxy_info,
            worker_note=worker_note,
        )
        if live_ref:
            live_ref[0].update(make_panel())

    search_diag: dict = {}
    search_fail: str | None = None
    try:
        with Live(make_panel(), refresh_per_second=4, console=console, transient=False) as live:
            live_ref.append(live)
            groups = await search_groups(api_key, on_progress=on_progress, diagnostics=search_diag)
            progress_state["cur"] = progress_state["total"]
            progress_state["found"] = len(groups)
            live.update(make_panel())
    except Exception as e:
        groups = []
        search_fail = str(e)

    out_path = Path("output") / "found_groups.json"
    out_path.parent.mkdir(exist_ok=True)
    if _snapshot_found_groups_before_overwrite(out_path):
        console.print(
            f"[dim]Предыдущий список сохранён:[/] [cyan]{_FOUND_GROUPS_PREVIOUS}[/] "
            f"и в [cyan]{_FOUND_GROUPS_ARCHIVE_DIR}/[/]"
        )
    out_path.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"\n[green]Найдено групп: {len(groups)}[/]")
    console.print("  [dim](после сбора: вейп-фильтр по keywords/exclude_keywords)[/]")
    if not groups:
        _emit_zero_search_diagnostics(search_diag, search_fail)
    from collections import Counter
    by_source = Counter(g.get("source", "?") for g in groups)
    for src, cnt in by_source.most_common():
        console.print(f"  [dim]{src}: {cnt}[/]")
    console.print(f"[dim]Сохранено в {out_path}[/]")
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


async def _run_scrape(
    sett: Settings | None = None,
    fixed_client=None,
) -> None:
    """Сбор базы пользователей. ``fixed_client`` — уже авторизованный Telethon (режим «отдельный»)."""
    db = get_db()
    await db.init()
    sett = sett or Settings()

    groups = _prompt_groups_list_source("Сбор базы пользователей")
    if not groups:
        console.print(
            "[yellow]Сбор не запущен:[/] нет списка групп, пустой [dim]found_groups.json[/], "
            "отмена ([cyan]0[/]) или ошибка файла. "
            "Нужен пункт [bold]1[/] главного меню (поиск) или в запросе источника групп — [bold]2[/]/[bold]3[/] "
            "(txt с [dim]t.me[/], см. [dim]group_links.txt[/])."
        )
        return

    console.print(f"[bold blue]Сбор базы из {len(groups)} групп[/]")
    limit = _prompt_nonneg_int("Лимит сообщений на группу", default=300, minimum=1, maximum=500_000)
    if fixed_client is not None:
        console.print(
            "[dim]Между строками прогресса возможна пауза: Telegram отдаёт историю не мгновенно.[/]"
        )

    if fixed_client is not None:
        pool = None
        max_concurrent = 1
        console.print(
            "[dim]Отдельная сессия: группы по одной, один и тот же клиент Telethon.[/]"
        )
    else:
        tcat_sc = build_telethon_catalog()
        if not tcat_sc:
            console.print("[red]Нет аккаунтов с api_id+api_hash для пула сбора.[/]")
            return
        print_account_catalog_table(console, tcat_sc)
        _, scrape_scope = prompt_account_scope(console, tcat_sc, title="Сбор базы: пул аккаунтов")
        if not scrape_scope:
            return
        with console.status("[bold]Загрузка пула аккаунтов…[/]", spinner="dots"):
            pool = AccountPool(only_session_names=scrape_scope)
            max_concurrent = max(1, len(pool.accounts))
            if (sett.scrape_session_name or "").strip():
                max_concurrent = 1
        if (sett.scrape_session_name or "").strip():
            console.print(
                "[dim]Одна закреплённая сессия — группы последовательно (без параллели).[/]"
            )
    sem = asyncio.Semaphore(max_concurrent)

    async def _scrape_one(i: int, g: dict):
        title = g.get("title", "?")
        raw_link = str(g.get("link") or "").strip()
        raw_id = g.get("id")
        id_fb = str(raw_id).strip() if raw_id is not None and str(raw_id).strip() else None
        if not normalize_scrape_target(raw_link, id_fb):
            return 0, 0
        async with sem:
            try:
                def on_progress(cur, tot):
                    pct = (cur / tot * 100) if tot else 0
                    line = f"  [dim]{escape(str(title))}: {cur}/{tot} ({pct:.1f}%)[/]"
                    if cur == 1 or cur % 50 == 0 or cur >= tot:
                        console.print(line)
                    else:
                        console.print(line, end="\r")
                hot, warm = await scrape_group(
                    raw_link,
                    limit=limit,
                    pool=pool,
                    settings=sett,
                    on_progress=on_progress,
                    client=fixed_client,
                    id_fallback=id_fb,
                )
                console.print(f"  [green]{escape(str(title))}: {hot} горячих, {warm} тёплых[/]")
                return hot, warm
            except Exception as e:
                console.print(f"  [red]{escape(str(title))}: Ошибка {escape(str(e))}[/]")
                return 0, 0
            finally:
                await asyncio.sleep(max(0.0, sett.delay_scrape_between_groups))

    tasks = [_scrape_one(i, g) for i, g in enumerate(groups)]
    results = await asyncio.gather(*tasks)
    total_hot = sum(r[0] for r in results)
    total_warm = sum(r[1] for r in results)
    console.print(f"\n[bold green]Итого: {total_hot} горячих, {total_warm} тёплых[/]")
    if pool is not None and total_hot + total_warm > 0:
        try:
            touch_accounts_last_use(pool.session_names_ordered(), kind="scrape")
        except Exception:
            pass
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


async def _run_scrape_single_account_branch() -> None:
    """П.2→1: общий аккаунт без прокси или отдельный вход."""
    console.print()
    console.print("[bold white]── Один аккаунт для сбора ──[/]")
    console.print(
        f"{_mk('1')} [bold]Общий[/]: выбрать аккаунт из accounts.json — сбор [bold]без прокси[/] (только этот session)"
    )
    console.print(
        f"{_mk('2')} [bold]Отдельный[/]: вход в консоли (api, телефон, код, 2FA); прокси — при входе и "
        f"повторно перед сбором (в т.ч. для сохранённой сессии)"
    )
    console.print(f"{_mk('0')} Назад")
    ch = Prompt.ask("Выбор", choices=["0", "1", "2"], default="0")
    if ch == "0":
        return
    if ch == "1":
        tcat = build_telethon_catalog()
        if not tcat:
            console.print(
                "[red]Нет аккаунтов в accounts.json.[/] Добавьте сессию: главное меню → [bold]9[/] → [bold]3[/]."
            )
            return
        print_account_catalog_table(console, tcat)
        pick = _prompt_nonneg_int(
            "id аккаунта из таблицы",
            default=1,
            minimum=1,
            maximum=len(tcat),
        )
        name = tcat[pick - 1].session_name
        if not name:
            console.print("[red]У записи нет session_name.[/]")
            return
        console.print(
            "[dim]Прокси для сбора отключён (пул и proxy в JSON для этого прогона не используются).[/]"
        )
        s_one = clone_settings(scrape_use_proxy=False, scrape_session_name=str(name))
        await _run_scrape(s_one)
        return

    logged = await login_client_for_one_off_scrape(console)
    if not logged:
        return
    client, meta = logged
    console.print()
    console.print(
        "[bold green]Вход в Telegram выполнен.[/]\n"
        "[dim]Сканирование не начинается само:[/] дальше тот же шаг, что и при обычном сборе — "
        "[bold]откуда брать группы[/]. Укажите [cyan]1[/] если есть [dim]output/found_groups.json[/] "
        "после поиска (главное меню → [cyan]1[/]), или [cyan]2[/]/[cyan]3[/] — txt со ссылками [dim]t.me[/] на строку."
    )
    sett = Settings()
    try:
        await _run_scrape(sett, fixed_client=client)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    if Confirm.ask("Добавить этот аккаунт в accounts.json?", default=False):
        upsert_telethon_account(
            meta["session_name"],
            meta["api_id"],
            meta["api_hash"],
            phone=meta.get("phone"),
            proxy=meta.get("proxy_url"),
        )
        console.print(f"[green]Записано в {accounts_json_path()}[/]")


def _run_scrape_entry() -> None:
    """Главное меню п.5: подменю сбора — один аккаунт или стандарт."""
    while True:
        console.print()
        console.print("[bold white]── Сбор базы пользователей ──[/]")
        console.print(
            f"{_mk('1')} Один аккаунт: общий (из списка, без прокси) или отдельный (вход в консоли + прокси опционально)"
        )
        console.print(f"{_mk('2')} Стандартный сбор (settings: пул аккаунтов и scrape_use_proxy / прокси)")
        console.print(f"{_mk('0')} Назад в главное меню")
        sub = Prompt.ask("Выбор", choices=["0", "1", "2"], default="2")
        if sub == "0":
            break
        try:
            if sub == "2":
                asyncio.run(_run_scrape())
            elif sub == "1":
                asyncio.run(_run_scrape_single_account_branch())
        except KeyboardInterrupt:
            console.print("\n[yellow]Прервано.[/]")
        except Exception as e:
            console.print(f"[red]Ошибка: {escape(str(e))}[/]")
        Prompt.ask("\n[dim]Нажмите Enter…[/]", default="")


def _join_group_link(g: dict) -> str | None:
    link = g.get("link") or g.get("id", "")
    if not link or "t.me" not in str(link):
        return None
    return str(link).strip()


async def _run_join_groups() -> None:
    """Вступить в группы — параллельно по аккаунтам, повтор на других при FAIL."""
    groups = _prompt_groups_list_source("Вступление в группы")
    if not groups:
        return
    count = _prompt_nonneg_int(
        "Сколько групп обработать",
        default=min(10, len(groups)),
        minimum=1,
        maximum=len(groups),
    )
    groups = groups[:count]
    valid = [g for g in groups if _join_group_link(g)]
    if not valid:
        console.print("[red]Нет валидных ссылок t.me в выбранном списке.[/]")
        return

    tcat = build_telethon_catalog()
    if not tcat:
        console.print("[red]Нет аккаунтов с api_id+api_hash.[/]")
        return
    print_account_catalog_table(console, tcat)
    _, join_scope = prompt_account_scope(console, tcat, title="Вступление в группы")
    if not join_scope:
        console.print("[red]Пустой набор аккаунтов.[/]")
        return

    mgr = InviteManager(only_session_names=join_scope)
    sett = mgr.settings
    session_names = mgr.pool.session_names_ordered()
    if not session_names:
        console.print("[red]Нет аккаунтов в accounts.json[/]")
        return

    n_acc = len(session_names)
    n_groups = len(valid)
    max_rounds = max(50, n_groups * (n_acc + 2))
    console.print(
        f"[dim]Аккаунтов: {n_acc}. "
        f"Режим: группы делятся между аккаунтами (round-robin), все аккаунты работают [bold]параллельно[/]. "
        f"После FAIL группа снова ставится на другой аккаунт (пока не исчерпаны). "
        f"Пауза у каждого аккаунта между своими вступлениями: {sett.delay_join_min}–{sett.delay_join_max} сек.[/]"
    )
    console.print(
        f"[dim]К обработке: [bold]{n_groups}[/] групп с ссылкой t.me; лимит раундов: {max_rounds}.[/]\n"
    )

    # (group_dict, frozenset уже пробовавших session_name)
    pending: list[tuple[dict, frozenset]] = [(g, frozenset()) for g in valid]
    ok_count = 0
    give_up: list[str] = []
    round_no = 0
    log_lock = asyncio.Lock()

    async def _log_line(msg: str) -> None:
        async with log_lock:
            console.print(msg)

    while pending and round_no < max_rounds:
        round_no += 1
        buckets: dict[str, list[tuple[dict, frozenset]]] = defaultdict(list)
        for idx, (g, tried) in enumerate(pending):
            candidates = [sn for sn in session_names if sn not in tried]
            if not candidates:
                title = (g.get("title") or "?")[:60]
                give_up.append(title)
                continue
            sn = candidates[idx % len(candidates)]
            buckets[sn].append((g, tried))

        if not buckets:
            break

        in_round = sum(len(v) for v in buckets.values())
        await _log_line(
            f"\n[bold cyan]━━ Раунд {round_no} ━━[/] "
            f"[dim]в очереди было групп:[/] [white]{len(pending)}[/] · "
            f"[dim]назначено в этом раунде:[/] [white]{in_round}[/] · "
            f"[dim]аккаунтов параллельно:[/] [white]{len(buckets)}[/]"
        )
        await _log_line(
            "[dim]Шаг 1:[/] распределение — каждой группе выбран аккаунт (ещё не пробовавший её); "
            "ниже по строкам на аккаунт — сколько у него вступлений в этом раунде."
        )
        dist_parts = [
            f"[yellow]{escape(str(sn))}[/][dim]: {len(ts)} шт.[/]"
            for sn, ts in sorted(buckets.items(), key=lambda x: x[0])
        ]
        await _log_line("  " + " · ".join(dist_parts))
        await _log_line(
            f"[dim]Шаг 2:[/] [dim]параллельный запуск — у каждого аккаунта своя очередь вступлений "
            f"([bold]по очереди[/] внутри аккаунта, между ними пауза {sett.delay_join_min}–{sett.delay_join_max} с).[/]"
        )

        async def _worker_join(sn: str, tasks: list[tuple[dict, frozenset]]) -> tuple[list[tuple[dict, frozenset]], int]:
            fails_local: list[tuple[dict, frozenset]] = []
            ok_local = 0
            total_sn = len(tasks)
            for k, (g, tried) in enumerate(tasks, start=1):
                link = _join_group_link(g)
                if not link:
                    continue
                title = (g.get("title") or "?")[:55]
                await _log_line(
                    f"  [cyan]▶[/] [dim]{escape(str(sn))}[/] [dim]({k}/{total_sn})[/] "
                    f"[white]вступаю в группу[/] — [dim]{escape(str(title))}[/]"
                )
                try:
                    ok, _used, fail_reason = await mgr.join_group_with_session(link, sn)
                except Exception as e:
                    await _log_line(
                        f"    [red]✗ исключение[/] [dim]{escape(str(sn))}[/] — {escape(str(title))}: "
                        f"[red]{escape(str(e))}[/]"
                    )
                    fails_local.append((g, tried | {sn}))
                    await asyncio.sleep(max(1, random.uniform(sett.delay_join_min, sett.delay_join_max)))
                    continue
                if ok:
                    ok_local += 1
                    await _log_line(
                        f"    [green]✓ OK[/] [dim]{escape(str(sn))} — {escape(str(title))}[/]"
                    )
                else:
                    await _log_line(
                        f"    [red]✗ FAIL[/] [dim]{escape(str(sn))} — {escape(str(title))}[/]"
                    )
                    if fail_reason:
                        await _log_line(f"      [dim]{escape(fail_reason)}[/]")
                    fails_local.append((g, tried | {sn}))
                if k < total_sn:
                    await _log_line(
                        f"    [dim]пауза {sett.delay_join_min}–{sett.delay_join_max} с (этот аккаунт)…[/]"
                    )
                await asyncio.sleep(max(1, random.uniform(sett.delay_join_min, sett.delay_join_max)))
            return fails_local, ok_local

        results = await asyncio.gather(
            *(_worker_join(sn, ts) for sn, ts in buckets.items())
        )
        pending = []
        round_ok = 0
        round_retry = 0
        for fails_part, ok_part in results:
            pending.extend(fails_part)
            ok_count += ok_part
            round_ok += ok_part
            round_retry += len(fails_part)
        retry_msg = (
            f" · [yellow]{round_retry} групп — повтор в следующем раунде с другими аккаунтами[/]"
            if round_retry
            else ""
        )
        await _log_line(
            f"[bold cyan]Раунд {round_no} завершён:[/] [green]+{round_ok} успешных[/]{retry_msg}"
            f" · [dim]в очереди сейчас:[/] [white]{len(pending)}[/]"
        )

    if round_no >= max_rounds and pending:
        console.print(f"[yellow]Остановка по лимиту раундов ({max_rounds}), не обработано: {len(pending)}[/]")
        for g, _ in pending[:15]:
            give_up.append((g.get("title") or "?")[:50])

    if give_up:
        console.print(f"[dim]Без успеха (все аккаунты перепробованы или лимит): {len(give_up)}[/]")

    console.print(f"\n[bold green]Успешных вступлений: {ok_count}[/] из {len(valid)} групп с валидной ссылкой")
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


async def _add_contacts_workflow(
    *,
    pool: bool,
    fixed_session: str | None = None,
    fixed_client: TelegramClient | None = None,
    session_client_settings: Settings | None = None,
    prefer_pool_for_read: bool = False,
    invite_pool_scope: frozenset[str] | None = None,
) -> None:
    """Общая логика: категория, список из БД, цикл AddContact."""
    db = get_db()
    await db.init()
    cat = Prompt.ask("Категория (hot/warm/all)", choices=["hot", "warm", "all"], default="hot")
    console.print()
    console.print("[bold]Кого брать из базы?[/]")
    console.print(
        f"{_mk('1')} Только [bold]не[/] помеченных «в контактах» [dim](ещё не проходили п.7)[/]"
    )
    console.print(
        f"{_mk('2')} [bold]Всех[/] в категории [dim](и помеченных, и нет — повтор AddContact в Telegram обычно безвреден)[/]"
    )
    console.print(
        f"{_mk('3')} [bold]Очередь privacy[/] [dim](рассылка не дошла из-за приватности — для контактов и повтора)[/]"
    )
    scope = Prompt.ask("Выбор", choices=["1", "2", "3"], default="1")
    exclude_added = scope == "1"
    cat_f = cat if cat != "all" else None
    if scope == "3":
        users = await db.get_users(
            category=cat_f,
            limit=50,
            exclude_invited=True,
            exclude_added_to_contacts=False,
            exclude_broadcast=True,
            exclude_privacy_blocked=False,
            only_privacy_retry=True,
            exclude_username_not_found=True,
        )
    else:
        users = await db.get_users(
            category=cat_f,
            limit=50,
            exclude_added_to_contacts=exclude_added,
            exclude_broadcast=False,
            exclude_username_not_found=True,
        )
    if not users:
        console.print("[yellow]Нет пользователей для добавления.[/]")
        return
    count = _prompt_nonneg_int(
        "Сколько добавить",
        default=min(10, len(users)),
        minimum=1,
        maximum=len(users),
    )
    users = users[:count]
    only_names = invite_pool_scope
    if only_names is None and fixed_session:
        only_names = frozenset([fixed_session])
    mgr = InviteManager(only_session_names=only_names)
    for u in users:
        uname = u.get("username") or (f"@{u.get('telegram_id')}" if u.get("telegram_id") else None)
        if not uname:
            continue
        ident = str(uname).lstrip("@")
        if not ident.isdigit():  # AddContact по username, не по id
            console.print(f"  Добавляю @{ident}...")
            if fixed_client is not None:
                ok = await mgr.add_to_contacts_with_client(fixed_client, ident)
            elif fixed_session:
                ok = await mgr.add_to_contacts_with_session(
                    ident,
                    fixed_session,
                    settings=session_client_settings,
                    prefer_pool_for_read=prefer_pool_for_read,
                )
            elif pool:
                ok = await mgr.add_to_contacts(ident)
            else:
                ok = False
            if ok:
                await db.mark_added_to_contacts(u["id"])
            console.print(f"    {'[green]OK[/]' if ok else '[red]FAIL[/]'}")
            delay = max(1, random.uniform(mgr.settings.delay_contact_min, mgr.settings.delay_contact_max))
            await asyncio.sleep(delay)


async def _run_add_contacts_one_account_sub() -> None:
    """П.4→1: общий аккаунт без прокси (как сбор) или отдельный вход."""
    console.print()
    console.print("[bold white]── Один аккаунт — контакты ──[/]")
    console.print(
        f"{_mk('1')} [bold]Общий[/]: выбрать аккаунт из accounts.json — [bold]без прокси[/] пула (только этот session)"
    )
    console.print(
        f"{_mk('2')} [bold]Отдельный[/]: вход в консоли (api, телефон, код, 2FA); прокси — при входе и перед операцией"
    )
    console.print(f"{_mk('0')} Назад")
    ch = Prompt.ask("Выбор", choices=["0", "1", "2"], default="0")
    if ch == "0":
        return
    if ch == "1":
        tcat = build_telethon_catalog()
        if not tcat:
            console.print(
                "[red]Нет аккаунтов в accounts.json.[/] Добавьте сессию: главное меню → [bold]9[/] → [bold]3[/]."
            )
            return
        print_account_catalog_table(console, tcat)
        pick = _prompt_nonneg_int(
            "id аккаунта из таблицы",
            default=1,
            minimum=1,
            maximum=len(tcat),
        )
        name = tcat[pick - 1].session_name
        if not name:
            console.print("[red]У записи нет session_name.[/]")
            return
        console.print(
            "[dim]Прокси пула для контактов отключён (как при сборе «общий»).[/]"
        )
        s_one = clone_settings(scrape_use_proxy=False, scrape_session_name=str(name))
        await _add_contacts_workflow(
            pool=False,
            fixed_session=str(name),
            session_client_settings=s_one,
            prefer_pool_for_read=True,
        )
        return

    logged = await login_client_for_one_off_scrape(console)
    if not logged:
        return
    client, meta = logged
    console.print()
    console.print(
        "[bold green]Вход в Telegram выполнен.[/]\n"
        "[dim]Дальше выберите категорию и число контактов из базы — добавление с этой сессии.[/]"
    )
    try:
        await _add_contacts_workflow(pool=False, fixed_client=client)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    if Confirm.ask("Добавить этот аккаунт в accounts.json?", default=False):
        upsert_telethon_account(
            meta["session_name"],
            meta["api_id"],
            meta["api_hash"],
            phone=meta.get("phone"),
            proxy=meta.get("proxy_url"),
        )
        console.print(f"[green]Записано в {accounts_json_path()}[/]")


async def _run_add_contacts() -> None:
    """Добавить в контакты (как п.5 сбор: один аккаунт / пул)."""
    console.print()
    console.print("[bold white]── Добавить в контакты ──[/]")
    console.print(
        f"{_mk('1')} Один аккаунт: общий (из списка, без прокси) или отдельный (вход в консоли + прокси)"
    )
    console.print(f"{_mk('2')} Пул аккаунтов [dim](ротация)[/]")
    console.print(f"{_mk('0')} Отмена")
    mode = Prompt.ask("Выбор", choices=["0", "1", "2"], default="2")
    if mode == "0":
        return
    if mode == "1":
        await _run_add_contacts_one_account_sub()
    else:
        tcat = build_telethon_catalog()
        if not tcat:
            console.print("[red]Нет аккаунтов с api_id+api_hash.[/]")
        else:
            print_account_catalog_table(console, tcat)
            _, cscope = prompt_account_scope(console, tcat, title="Пул для добавления в контакты")
            if cscope:
                await _add_contacts_workflow(pool=True, invite_pool_scope=cscope)
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


async def _run_invite() -> None:
    """Пригласить в канал — напрямую из контактов аккаунта."""
    channel = strip_c0_controls(Prompt.ask("Username канала/группы (например @channel)").strip())
    channel = channel.lstrip("@").strip()
    if not channel:
        console.print("[red]Укажите username канала.[/]")
        return
    limit = _prompt_nonneg_int(
        "Сколько контактов пригласить (всего)",
        default=20,
        minimum=1,
        maximum=10_000,
    )
    tcat = build_telethon_catalog()
    if not tcat:
        console.print("[red]Нет аккаунтов с api_id+api_hash.[/]")
        return
    print_account_catalog_table(console, tcat)
    _, inv_scope = prompt_account_scope(console, tcat, title="Приглашение в канал: аккаунты")
    if not inv_scope:
        console.print("[red]Пустой набор аккаунтов.[/]")
        return
    mgr = InviteManager(only_session_names=inv_scope)
    sett = mgr.settings
    session_names = mgr.pool.session_names_ordered()
    if not session_names:
        console.print("[red]Нет аккаунтов в accounts.json[/]")
        return

    parallel = False
    if len(session_names) > 1:
        console.print()
        console.print(f"{_mk('1')} Один аккаунт [dim](least-used из пула)[/]")
        console.print(
            f"{_mk('2')} Параллельно — все [bold]{len(session_names)}[/] аккаунтов "
            f"[dim](лимит {limit} делится между ними поровну; у каждого своя адресная книга; "
            f"между батчами по {sett.delay_invite_min}–{sett.delay_invite_max} с)[/]"
        )
        parallel = Prompt.ask("Режим", choices=["1", "2"], default="2") == "2"
    else:
        console.print(
            f"[dim]Один аккаунт [cyan]{escape(session_names[0])}[/] · канал @{escape(channel)}[/]"
        )

    if len(session_names) > 1 and not parallel:
        console.print(
            f"[dim]Один прогон: контакты с одного аккаунта (least-used) → @{escape(channel)}[/]"
        )
    elif parallel:
        console.print(
            f"[dim]Параллельно в @{escape(channel)}: суммарно до {limit} приглашений по контактам аккаунтов.[/]"
        )
    else:
        console.print(f"[dim]Контакты аккаунта → @{escape(channel)}[/]")

    if not Confirm.ask("Продолжить?"):
        return

    with console.status("[bold]Приглашение контактов в канал…[/]", spinner="dots"):
        if parallel:
            invited, breakdown = await mgr.invite_contacts_to_channel_parallel(
                f"@{channel}", total_limit=limit, batch_size=10
            )
        else:
            invited, session = await mgr.invite_contacts_to_channel(
                f"@{channel}", limit=limit, batch_size=10
            )
            breakdown = [(session, invited)] if session else []

    console.print(f"\n[bold green]Приглашено: {invited} контактов[/]")
    if parallel:
        for sn, n in breakdown:
            console.print(f"  [dim]{escape(str(sn))}:[/] [white]{n}[/]")
    elif breakdown and breakdown[0][0]:
        console.print(f"  [dim]аккаунт:[/] [white]{escape(str(breakdown[0][0]))}[/]")
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


async def _run_check_proxies() -> None:
    """Проверить работоспособность прокси из пула."""
    proxies = load_proxy_pool_from_config()
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
    proxies = load_proxy_pool_from_config()
    if not proxies:
        console.print("[red]Нет прокси. Добавьте в config/proxies.txt или settings.json[/]")
        return
    tcat = build_telethon_catalog()
    if not tcat:
        console.print("[red]Нет аккаунтов с api_id+api_hash в accounts.json[/]")
        return
    print_account_catalog_table(console, tcat)
    _, pscope = prompt_account_scope(console, tcat, title="Назначение прокси (round-robin)")
    if not pscope:
        console.print("[red]Пустой набор.[/]")
        return
    bundle_rows = bundle_round_robin_account_rows(load_accounts_all())
    n_match = sum(
        1
        for r in bundle_rows
        if (r.get("session_name") or "").strip() in pscope
    )
    console.print(
        f"[dim]В пуле прокси: {len(proxies)} шт. Аккаунтов в выборе (с session в JSON): {n_match}.[/]"
    )
    if not Confirm.ask(
        "Назначить каждому выбранному аккаунту прокси по round-robin?"
    ):
        return
    ok, msg = assign_proxies_round_robin_to_accounts(
        Settings(), only_session_names=pscope
    )
    if ok:
        try:
            touch_accounts_last_use(list(pscope), kind="proxy_assign")
        except Exception:
            pass
        console.print(
            f"[green]Прокси назначены:[/] у каждого аккаунта в [bold]accounts.json[/] обновлено поле [bold]proxy[/] "
            f"(round-robin из пула). Файл: [cyan]{msg}[/]"
        )
    else:
        console.print(f"[red]{msg}[/]")
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


async def _run_browse_users_db() -> None:
    """Поиск по username и листинг users порциями."""
    console.print()
    console.print("[bold white]── База пользователей (SQLite) ──[/]")
    db = get_db()
    with console_loading(console, "Загрузка…"):
        await db.init()
    console.print(f"{_mk('1')} Поиск и просмотр по username")
    console.print(
        f"{_mk('2')} Сбросить метку «username не найден в Telegram» "
        "[dim](все записи с username_not_found_at)[/]"
    )
    console.print(
        f"{_mk('3')} Экспорт username в .txt [dim](уникальные строки, дубли и разный регистр схлопываются)[/]"
    )
    sub_hub = Prompt.ask("Действие", choices=["1", "2", "3"], default="1")
    if sub_hub == "3":
        cat_ex = Prompt.ask("Категория", choices=["all", "hot", "warm"], default="all")
        cat_ex_val = None if cat_ex == "all" else cat_ex
        ex_nf = Confirm.ask(
            "Исключить строки с меткой «username не найден в Telegram» [dim](username_not_found_at)[/]?",
            default=True,
        )
        default_out = _PROJECT_ROOT / "output" / "usernames_unique.txt"
        raw_path = strip_c0_controls(
            Prompt.ask("Путь к .txt для сохранения", default=str(default_out)).strip()
        )
        if not raw_path:
            console.print("[dim]Отмена.[/]")
            Prompt.ask("\n[dim]Нажмите Enter…[/]", default="")
            return
        out_path = Path(raw_path).expanduser()
        names = await db.unique_usernames_for_export(
            category=cat_ex_val,
            exclude_username_not_found=ex_nf,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(names) + ("\n" if names else "")
        out_path.write_text(body, encoding="utf-8")
        console.print(
            f"[green]Готово:[/] уникальных username [bold]{len(names)}[/] → "
            f"[cyan]{escape(str(out_path.resolve()))}[/]"
        )
        console.print("[dim]Формат: по одному нику в строке, без @; пустые username в БД не попадают.[/]")
        Prompt.ask("\n[dim]Нажмите Enter…[/]", default="")
        return
    if sub_hub == "2":
        n_marked = await db.count_username_not_found()
        if n_marked == 0:
            console.print("[dim]Нет записей с этой меткой.[/]")
            Prompt.ask("\n[dim]Нажмите Enter…[/]", default="")
            return
        console.print(f"[dim]Записей с меткой:[/] [bold]{n_marked}[/]")
        if not Confirm.ask(
            "Сбросить метку у всех этих строк? Они снова попадут в рассылку и в выборку контактов "
            "(если совпадают категория и прочие фильтры).",
            default=False,
        ):
            Prompt.ask("\n[dim]Нажмите Enter…[/]", default="")
            return
        cleared = await db.clear_username_not_found_all()
        console.print(f"[green]Сброшено меток:[/] [bold]{cleared}[/]")
        Prompt.ask("\n[dim]Нажмите Enter…[/]", default="")
        return

    cat = Prompt.ask("Категория", choices=["all", "hot", "warm"], default="all")
    list_mode = Prompt.ask(
        "Кого показывать",
        choices=["all", "privacy_queue"],
        default="all",
    )
    needle = strip_c0_controls(
        Prompt.ask("Подстрока username [dim](пусто = все; без @)[/]", default="").strip()
    )
    needle_arg = needle if needle else None

    async def _refresh_total() -> int:
        if list_mode == "privacy_queue":
            return await db.count_privacy_queue(username_contains=needle_arg, category=cat)
        return await db.count_users_search(username_contains=needle_arg, category=cat)

    total = await _refresh_total()
    console.print(f"[dim]Найдено записей:[/] [bold]{total}[/]")
    if total == 0:
        Prompt.ask("\n[dim]Нажмите Enter…[/]", default="")
        return

    offset = 0
    page_size = 20
    while True:
        if list_mode == "privacy_queue":
            rows = await db.list_privacy_queue_page(
                username_contains=needle_arg,
                category=cat,
                offset=offset,
                limit=page_size,
            )
        else:
            rows = await db.list_users_search_page(
                username_contains=needle_arg,
                category=cat,
                offset=offset,
                limit=page_size,
            )
        if not rows:
            console.print("[dim]Конец списка по текущему фильтру.[/]")
            break
        end = min(offset + len(rows), total)
        title_suffix = "privacy-очередь" if list_mode == "privacy_queue" else "users"
        table = Table(title=f"{title_suffix} [dim]{offset + 1}–{end} из {total}[/]")
        table.add_column("id", style="dim", width=7)
        table.add_column("username", style="cyan", max_width=28)
        table.add_column("telegram_id", style="dim", max_width=14)
        table.add_column("cat", width=6)
        table.add_column("first_seen", style="dim", max_width=20)
        if list_mode == "privacy_queue":
            table.add_column("privacy_at", style="yellow", max_width=20)
        for r in rows:
            fs = (r.get("first_seen_at") or "")[:19]
            cells = [
                str(r.get("id")),
                str(r.get("username") or "—"),
                str(r.get("telegram_id") or "—"),
                str(r.get("category") or "—"),
                fs,
            ]
            if list_mode == "privacy_queue":
                cells.append((r.get("broadcast_privacy_blocked_at") or "")[:19])
            table.add_row(*cells)
        console.print(table)
        console.print(
            "[dim]Enter — следующие 20 · введите новую подстроку username — новый поиск · q — выход[/]"
        )
        nxt = strip_c0_controls(Prompt.ask("Далее", default="").strip())
        low = nxt.lower()
        if low in ("q", "quit", "й", "exit"):
            break
        if nxt:
            needle = nxt.lstrip("@")
            needle_arg = needle if needle else None
            total = await _refresh_total()
            console.print(f"[dim]Найдено записей:[/] [bold]{total}[/]")
            offset = 0
            if total == 0:
                break
            continue
        offset += page_size
        if offset >= total:
            console.print("[dim]Все записи показаны.[/]")
            break

    Prompt.ask("\n[dim]Нажмите Enter…[/]", default="")


async def _run_stats() -> None:
    """Статистика базы."""
    db = get_db()
    with console_loading(console, "Статистика…"):
        await db.init()
        hot, warm = await db.count_users()
        privacy_queue_n = await db.count_privacy_queue()
        username_nf_n = await db.count_username_not_found()

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
    table.add_row("", "")
    table.add_row("[bold]Рассылка[/]", "")
    table.add_row("  Очередь privacy (не доставлено)", str(privacy_queue_n))
    table.add_row("  Метка «username не найден в TG»", str(username_nf_n))
    console.print(table)
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


def _view_groups_table_once(found_path: Path, groups: list[dict]) -> None:
    """Однократный вывод таблицы групп."""
    limit = _prompt_nonneg_int("Сколько показать (0 = все)", default=30, allow_zero=True, minimum=0)
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
    console.print(
        "[dim]Очистить весь список — [bold]главное меню → a[/] или подтвердите ниже.[/]"
    )
    if Confirm.ask("Очистить found_groups.json (все записи)?", default=False):
        found_path.parent.mkdir(parents=True, exist_ok=True)
        if _snapshot_found_groups_before_overwrite(found_path):
            console.print(
                f"[dim]Копия до очистки:[/] [cyan]{_FOUND_GROUPS_PREVIOUS}[/] "
                f"([cyan]{_FOUND_GROUPS_ARCHIVE_DIR}/[/])"
            )
        found_path.write_text("[]\n", encoding="utf-8")
        console.print("[green]Список найденных групп очищен.[/]")


def _run_export_groups_txt(found_path: Path, groups: list[dict]) -> None:
    """Экспорт ссылок в txt."""
    default_out = Path("output") / "found_groups_export.txt"
    raw = strip_c0_controls(
        Prompt.ask("Путь к .txt для сохранения", default=str(default_out)).strip()
    )
    if not raw:
        console.print("[dim]Отмена.[/]")
        return
    out_p = Path(raw).expanduser()
    ok, msg, n = export_groups_to_txt(groups, out_p)
    if ok:
        console.print(f"[green]Экспортировано ссылок:[/] [bold]{n}[/] → [cyan]{escape(msg)}[/]")
    else:
        console.print(f"[red]{escape(msg)}[/]")


def _run_import_groups_txt(found_path: Path) -> None:
    """Импорт ссылок из txt в found_groups.json."""
    console.print(
        "[dim]Формат: как [bold]group_links.txt[/] — по одной ссылке [bold]t.me[/] / [bold]telegram.me[/] на строку; "
        "строки с # в начале пропускаются.[/]"
    )
    raw = strip_c0_controls(Prompt.ask("Полный путь к .txt", default="").strip())
    if not raw:
        console.print("[dim]Отмена.[/]")
        return
    txt_p = Path(raw).expanduser()
    if not txt_p.is_file():
        console.print(f"[red]Файл не найден: {escape(str(txt_p))}[/]")
        return
    mode = Prompt.ask(
        "Режим",
        choices=["replace", "append"],
        default="append",
    )
    if mode == "replace" and found_path.is_file():
        if not Confirm.ask(
            "Заменить весь found_groups.json содержимым из txt? (будет снимок копии, если список не пуст.)",
            default=False,
        ):
            console.print("[dim]Отменено.[/]")
            return
        if _snapshot_found_groups_before_overwrite(found_path):
            console.print(
                f"[dim]Копия текущего JSON:[/] [cyan]{_FOUND_GROUPS_PREVIOUS}[/] "
                f"и [cyan]{_FOUND_GROUPS_ARCHIVE_DIR}/[/]"
            )
    elif mode == "append" and found_path.is_file():
        try:
            body = found_path.read_text(encoding="utf-8").strip()
            data = json.loads(body) if body else []
            if isinstance(data, list) and len(data) > 0:
                if not Confirm.ask(
                    f"Добавить ссылки из txt к текущим {len(data)} группам (дубликаты по ссылке уберутся)?",
                    default=True,
                ):
                    console.print("[dim]Отменено.[/]")
                    return
        except (OSError, json.JSONDecodeError):
            if not Confirm.ask("found_groups.json повреждён или пуст — перезаписать из txt?", default=True):
                return

    with console.status("[bold]Импорт…[/]", spinner="dots"):
        ok, msg, total = import_txt_to_found_groups(txt_p, found_path, mode=mode)
    if ok:
        console.print(
            f"[green]Готово:[/] в [cyan]{escape(msg)}[/] сейчас [bold]{total}[/] групп "
            f"([dim]режим: {mode}[/])"
        )
    else:
        console.print(f"[red]{escape(msg)}[/]")


def _run_view_groups() -> None:
    """Просмотр / экспорт / импорт найденных групп (found_groups.json)."""
    found_path = Path("output") / "found_groups.json"
    while True:
        console.print()
        console.print("[bold white]── Найденные группы ──[/]")
        exists = found_path.is_file()
        groups: list[dict] = []
        err_read: str | None = None
        if exists:
            loaded, err_read = load_found_groups_list(found_path)
            if loaded is None:
                console.print(f"[red]Ошибка чтения found_groups.json: {escape(err_read or '')}[/]")
            else:
                groups = loaded
        n = len(groups)
        console.print(
            f"[dim]Файл:[/] [cyan]{found_path}[/] · "
            f"[dim]записей:[/] {'[yellow]0[/]' if n == 0 else f'[green]{n}[/]'}"
        )
        console.print(f"{_mk('1')} Показать таблицу в консоли")
        console.print(f"{_mk('2')} Экспорт в текстовый файл [dim](ссылки t.me, как group_links.txt)[/]")
        console.print(f"{_mk('3')} Импорт из текстового файла [dim](replace или append)[/]")
        console.print(f"{_mk('0')} Назад в главное меню")
        console.print()
        sub = Prompt.ask("Выбор", choices=["0", "1", "2", "3"], default="0")
        if sub == "0":
            break
        if sub == "1":
            if not exists:
                console.print("[red]Файла нет. Сначала пункт [bold]1[/] главного меню (поиск) или импорт txt ([bold]3[/]).[/]")
            elif not groups:
                console.print("[yellow]Список групп пуст. Импортируйте txt ([bold]3[/]) или выполните поиск.[/]")
            else:
                _view_groups_table_once(found_path, groups)
            Prompt.ask("\n[dim]Enter — продолжить[/]", default="")
        elif sub == "2":
            if not groups:
                console.print("[yellow]Нечего экспортировать — список пуст.[/]")
            else:
                _run_export_groups_txt(found_path, groups)
            Prompt.ask("\n[dim]Enter — продолжить[/]", default="")
        elif sub == "3":
            _run_import_groups_txt(found_path)
            loaded, _ = load_found_groups_list(found_path)
            if loaded is not None:
                groups = loaded
            Prompt.ask("\n[dim]Enter — продолжить[/]", default="")


def _run_clear_found_groups() -> None:
    """Очистить output/found_groups.json (список найденных групп для сбора/вступления)."""
    found_path = Path("output") / "found_groups.json"
    if not found_path.is_file():
        console.print("[yellow]Файл output/found_groups.json не найден — нечего очищать.[/]")
        Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")
        return
    try:
        raw = found_path.read_text(encoding="utf-8").strip()
        data = json.loads(raw) if raw else []
        n = len(data) if isinstance(data, list) else 0
    except json.JSONDecodeError:
        n = None
        console.print("[yellow]Файл повреждён (невалидный JSON) — будет записан пустой список.[/]")
    msg = f"Удалить все записи в found_groups.json{f' ({n} групп)' if n is not None else ''}?"
    if not Confirm.ask(msg, default=False):
        console.print("[dim]Отменено.[/]")
        Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")
        return
    found_path.parent.mkdir(parents=True, exist_ok=True)
    if _snapshot_found_groups_before_overwrite(found_path):
        console.print(
            f"[dim]Копия до очистки:[/] [cyan]{_FOUND_GROUPS_PREVIOUS}[/] "
            f"и [cyan]{_FOUND_GROUPS_ARCHIVE_DIR}/[/] — можно скопировать обратно в found_groups.json[/]"
        )
    found_path.write_text("[]\n", encoding="utf-8")
    console.print("[green]Список найденных групп очищен.[/] Запустите п.1 для нового поиска.")
    Prompt.ask("\n[dim]Нажмите Enter для возврата в меню[/]", default="")


def run_menu() -> None:
    """Запуск главного меню."""
    _sett = Settings()
    if _sett.sync_sessions_on_startup:
        try:
            n_add, warns = sync_sessions_dir_to_accounts(_sett)
            if n_add:
                console.print(
                    f"[dim]sync_sessions_on_startup:[/] [green]+{n_add}[/] "
                    f"аккаунт(ов) → accounts.json (из папки сессий + .json)"
                )
            for w in warns[:12]:
                console.print(f"[dim]sync_sessions:[/] [yellow]{escape(str(w))}[/]")
            if len(warns) > 12:
                console.print(f"[dim]… ещё предупреждений: {len(warns) - 12}[/]")
        except Exception as e:
            console.print(f"[red]sync_sessions_on_startup: {escape(str(e))}[/]")
        console.print()

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
                _run_view_groups()
            elif choice == "3":
                asyncio.run(_run_stats())
            elif choice == "4":
                asyncio.run(_run_browse_users_db())
            elif choice == "5":
                _run_scrape_entry()
            elif choice == "6":
                asyncio.run(_run_join_groups())
            elif choice == "7":
                asyncio.run(_run_add_contacts())
            elif choice == "8":
                asyncio.run(_run_invite())
            elif choice == "9":
                _run_system_hub_submenu()
            elif choice == "a":
                _run_clear_found_groups()
        except KeyboardInterrupt:
            console.print("\n[yellow]Прервано.[/]")
        except Exception as e:
            console.print(f"[red]Ошибка: {escape(str(e))}[/]")
        console.print()
