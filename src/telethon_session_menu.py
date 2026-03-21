"""Консоль: сессии Telethon (.session) и accounts.json."""
import secrets
import shutil
from pathlib import Path

from rich.prompt import Prompt, Confirm
from rich.table import Table

from src.cli_input import digits_only, parse_api_id_digits, strip_c0_controls
from src.config import (
    Settings,
    accounts_json_path,
    effective_2fa_password,
    is_placeholder_proxy_url,
    load_accounts,
    load_session_bind_specs_from_file,
    proxy_url_to_telethon,
    session_bind_file_path,
    telethon_session_dir_path,
    upsert_telethon_account,
)


def _session_dir_label() -> str:
    return str(telethon_session_dir_path()).replace("\\", "/")


def _sessions_dir() -> Path:
    return telethon_session_dir_path()


def _unique_session_stem_from_phone(phone: str) -> str:
    """
    Имя файла без расширения для .session (локально на диске, не @username в Telegram).
    """
    digits = digits_only(phone)
    tail = digits[-8:] if len(digits) >= 4 else (digits or "user")
    base = f"tg_{tail}"
    d = _sessions_dir()
    stem = base
    for _ in range(32):
        if not (d / f"{stem}.session").is_file():
            return stem
        stem = f"{base}_{secrets.token_hex(2)}"
    return f"tg_{secrets.token_hex(8)}"


def _session_paths() -> list[Path]:
    d = _sessions_dir()
    if not d.is_dir():
        return []
    return sorted(d.glob("*.session"))


def _append_account(
    session_name: str,
    api_id: int,
    api_hash: str,
    phone: str | None = None,
    proxy: str | None = None,
) -> None:
    upsert_telethon_account(
        session_name, api_id, api_hash, phone=phone, proxy=proxy
    )


async def _list_sessions_console(console) -> None:
    files = {p.stem for p in _session_paths()}
    accs = load_accounts()
    dlabel = _session_dir_label()

    table = Table(title="Сессии Telethon")
    table.add_column("session_name", style="cyan")
    table.add_column(f".session в {dlabel}", style="green")
    table.add_column("В accounts.json", style="yellow")
    table.add_column("phone", style="dim")
    names = sorted(files | {a.get("session_name", "") for a in accs if a.get("session_name")})
    names = [n for n in names if n]
    if not names:
        console.print(f"[yellow]Нет .session в {dlabel} и нет аккаунтов в accounts.json[/]")
        return
    by_name = {a.get("session_name"): a for a in accs}
    for name in names:
        has_file = "да" if name in files else "нет"
        in_json = "да" if name in by_name else "нет"
        phone = (by_name.get(name) or {}).get("phone") or "—"
        table.add_row(name, has_file, in_json, str(phone))
    console.print(table)
    console.print(
        f"[dim]Файлы лежат в [cyan]{dlabel}[/]. "
        "Назначение прокси (меню 8→1) затрагивает только строки в accounts.json — "
        "сначала привяжите .session через п.2 или п.3. "
        "Нужны api_id и api_hash с https://my.telegram.org.[/]"
    )


async def _bind_session_console(console) -> None:
    raw = strip_c0_controls(
        Prompt.ask(
            f"Путь к .session или имя без расширения (если файл уже в {_session_dir_label()}/)",
        ).strip()
    )
    if not raw:
        return
    p = Path(raw)
    if p.suffix.lower() == ".session" and p.is_file():
        session_name = p.stem
        dest = _sessions_dir() / f"{session_name}.session"
        _sessions_dir().mkdir(parents=True, exist_ok=True)
        if p.resolve() != dest.resolve():
            shutil.copy2(p, dest)
            console.print(f"[green]Скопировано в {dest}[/]")
    else:
        # только имя — файл должен лежать в sessions/
        session_name = Path(raw).stem
        dest = _sessions_dir() / f"{session_name}.session"
        if not dest.is_file():
            console.print(
                f"[red]Нет файла {dest}. Положите .session в {_session_dir_label()}/ или укажите полный путь.[/]"
            )
            return

    api_id_s = strip_c0_controls(Prompt.ask("api_id (число с my.telegram.org)").strip())
    api_hash = strip_c0_controls(Prompt.ask("api_hash").strip())
    api_id = parse_api_id_digits(api_id_s)
    if api_id is None:
        console.print("[red]api_id должен быть числом[/]")
        return
    phone = strip_c0_controls(Prompt.ask("Телефон (опционально)", default="").strip()) or None
    proxy = strip_c0_controls(Prompt.ask("Прокси URL (опционально)", default="").strip()) or None

    if Confirm.ask(f"Добавить/обновить аккаунт «{session_name}» в accounts.json?", default=True):
        _append_account(session_name, api_id, api_hash, phone=phone, proxy=proxy)
        console.print(f"[green]Готово: {accounts_json_path()}[/]")


async def _new_login_console(console) -> None:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    api_id_s = strip_c0_controls(Prompt.ask("api_id").strip())
    api_id = parse_api_id_digits(api_id_s)
    if api_id is None:
        console.print("[red]api_id должен быть числом[/]")
        return
    api_hash = strip_c0_controls(Prompt.ask("api_hash").strip())
    phone = strip_c0_controls(
        Prompt.ask(
            "Телефон в международном формате (+код страны, напр. +375…, +7…, +95…)"
        ).strip()
    )
    if not phone:
        console.print("[red]Нужен телефон[/]")
        return

    auto_name = _unique_session_stem_from_phone(phone)
    session_name = strip_c0_controls(
        Prompt.ask(
            f"Имя файла в {_session_dir_label()}/ (Enter = автоматически: {auto_name})",
            default=auto_name,
        ).strip()
    )
    if not session_name or "/" in session_name or "\\" in session_name:
        session_name = auto_name

    _sessions_dir().mkdir(parents=True, exist_ok=True)
    session_base = str(_sessions_dir() / session_name)

    def code_cb() -> str:
        return digits_only(Prompt.ask("Код из Telegram (SMS или приложение)"))

    def password_cb() -> str:
        # Без ручного ввода: settings или дефолт из config
        return effective_2fa_password()

    client = TelegramClient(session_base, api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            sent = await client.send_code_request(phone)
            try:
                await client.sign_in(
                    phone,
                    code_cb(),
                    phone_code_hash=sent.phone_code_hash,
                )
            except SessionPasswordNeededError:
                await client.sign_in(password=password_cb())
        me = await client.get_me()
        console.print(f"[green]Авторизовано: {getattr(me, 'username', None) or me.id}[/]")
        await client.disconnect()
    except Exception as e:
        console.print(f"[red]Ошибка: {e}[/]")
        try:
            await client.disconnect()
        except Exception:
            pass
        return

    proxy = strip_c0_controls(Prompt.ask("Прокси URL (опционально)", default="").strip()) or None
    if Confirm.ask("Записать аккаунт в accounts.json?", default=True):
        _append_account(session_name, api_id, api_hash, phone=phone, proxy=proxy)
        console.print(f"[green]Сохранено в {accounts_json_path()}[/]")


async def login_client_for_one_off_scrape(console):
    """
    Разовая авторизация для сбора базы (меню 2→1→отдельный).
    Порядок: api → телефон → [прокси да/нет] → код → 2FA; имя .session подставляется само.

    Возвращает (TelegramClient, meta) с уже подключённым клиентом; disconnect — у вызывающего.
    meta: session_name, api_id, api_hash, phone, proxy_url (str | None).
    """
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    settings = Settings()
    pair = _ask_api_id_hash_or_defaults(console, settings)
    if not pair:
        return None
    api_id, api_hash = pair

    phone = strip_c0_controls(
        Prompt.ask(
            "Телефон в международном формате (+код страны, напр. +375…, +7…, +95…)"
        ).strip()
    )
    if not phone:
        console.print("[red]Нужен телефон[/]")
        return None

    proxy_url: str | None = None
    if Confirm.ask("Использовать прокси для Telegram (этот сбор)?", default=False):
        raw = strip_c0_controls(Prompt.ask("Прокси URL (socks5:// или http://)", default="").strip())
        if raw and not is_placeholder_proxy_url(raw):
            proxy_url = raw

    session_name = _unique_session_stem_from_phone(phone)
    console.print(
        f"[dim]Вход по номеру и коду — как в приложении Telegram. "
        f"Ключ сохранится в файл [cyan]{_session_dir_label()}/{session_name}.session[/] "
        f"(это не логин и не @username, только чтобы не вводить код каждый раз).[/]"
    )

    proxy_tg = proxy_url_to_telethon(proxy_url)
    _sessions_dir().mkdir(parents=True, exist_ok=True)
    session_base = str(_sessions_dir() / session_name)

    def code_cb() -> str:
        return digits_only(Prompt.ask("Код из Telegram (SMS или приложение)"))

    def password_cb() -> str:
        manual = strip_c0_controls(
            Prompt.ask(
                "Пароль облачного 2FA (или Enter — взять из settings / встроенный дефолт)",
                default="",
            ).strip()
        )
        if manual:
            return manual
        return effective_2fa_password(settings)

    client = TelegramClient(session_base, api_id, api_hash, proxy=proxy_tg)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            sent = await client.send_code_request(phone)
            try:
                await client.sign_in(
                    phone,
                    code_cb(),
                    phone_code_hash=sent.phone_code_hash,
                )
            except SessionPasswordNeededError:
                await client.sign_in(password=password_cb())
        me = await client.get_me()
        console.print(f"[green]Авторизовано: {getattr(me, 'username', None) or me.id}[/]")
    except Exception as e:
        console.print(f"[red]Ошибка входа: {e}[/]")
        try:
            await client.disconnect()
        except Exception:
            pass
        return None

    meta = {
        "session_name": session_name,
        "api_id": api_id,
        "api_hash": api_hash,
        "phone": phone,
        "proxy_url": proxy_url,
    }
    return client, meta


def _ask_api_id_hash_or_defaults(console, settings: Settings) -> tuple[int, str] | None:
    """api_id + api_hash из settings или один запрос в консоль."""
    aid = settings.default_telethon_api_id
    hsh = settings.default_telethon_api_hash
    if aid is not None and hsh:
        console.print(f"[dim]В settings.json (telethon_default_api): api_id={aid}[/]")
        if Confirm.ask("Использовать этот api_id и api_hash?", default=True):
            return aid, hsh
    console.print(
        "[dim]Можно задать постоянно в settings.json → telethon_default_api "
        "(см. settings.json.example).[/]"
    )
    aid = parse_api_id_digits(strip_c0_controls(Prompt.ask("api_id (my.telegram.org)").strip()))
    if aid is None:
        console.print("[red]Некорректный api_id[/]")
        return None
    hsh = strip_c0_controls(Prompt.ask("api_hash").strip())
    if not hsh:
        console.print("[red]Пустой api_hash[/]")
        return None
    return aid, hsh


async def _auto_bind_sessions_console(console) -> None:
    """Массовая запись в accounts.json: из папки сессий или из session_bind.txt."""
    settings = Settings()
    dlabel = _session_dir_label()
    bind_path = session_bind_file_path()

    console.print("\n[bold]Автопривязка в accounts.json[/]")
    console.print(
        f"[[1]] Все .session в [cyan]{dlabel}[/], которых ещё нет в accounts.json (один api для всех)\n"
        f"[[2]] Список из [cyan]{bind_path}[/] (как proxies.txt: имя или имя:api_id:api_hash:телефон)\n"
        "[[0]] Отмена"
    )
    mode = Prompt.ask("Режим", choices=["0", "1", "2"], default="0")
    if mode == "0":
        return

    stems = {p.stem for p in _session_paths()}
    in_json = {
        a.get("session_name")
        for a in load_accounts()
        if a.get("session_name")
    }

    if mode == "1":
        missing = sorted(stems - in_json)
        if not missing:
            console.print(
                "[yellow]Нечего добавлять: для каждого .session уже есть запись в accounts.json "
                "или в папке нет .session.[/]"
            )
            return
        preview = ", ".join(missing[:15])
        if len(missing) > 15:
            preview += f" … (+{len(missing) - 15})"
        console.print(f"[dim]Будет добавлено аккаунтов: {len(missing)} — {preview}[/]")
        pair = _ask_api_id_hash_or_defaults(console, settings)
        if not pair:
            return
        aid, ahash = pair
        if not Confirm.ask("Записать в accounts.json?", default=True):
            return
        for name in missing:
            upsert_telethon_account(name, aid, ahash)
        console.print(f"[green]Готово: {accounts_json_path()}[/]")
        return

    # mode == 2 — файл
    specs = load_session_bind_specs_from_file()
    if not specs:
        console.print(
            f"[red]Файл пуст или отсутствует: {bind_path}[/]\n"
            f"[dim]Скопируйте config/session_bind.txt.example → session_bind.txt и заполните.[/]"
        )
        return

    errs: list[str] = []
    to_apply: list[tuple[str, int, str, str | None]] = []
    for spec in specs:
        name = spec["session_name"]
        aid = spec["api_id"]
        ahash = spec["api_hash"]
        phone = spec.get("phone")
        if aid is None or not ahash:
            aid = settings.default_telethon_api_id
            ahash = settings.default_telethon_api_hash
        if aid is None or not ahash:
            errs.append(f"{name}: нет api_id/api_hash (в строке или telethon_default_api)")
            continue
        to_apply.append((name, int(aid), str(ahash), phone))

    if errs:
        for e in errs:
            console.print(f"  [red]{e}[/]")
    if not to_apply:
        console.print("[red]Нечего записать (исправьте ошибки выше).[/]")
        return

    console.print(f"[dim]Записей к применению: {len(to_apply)} (файл {bind_path})[/]")
    for name, _, _, _ in to_apply[:8]:
        has_f = "да" if name in stems else "нет .session (строка всё равно будет в JSON)"
        console.print(f"  • {name}  (файл: {has_f})")
    if len(to_apply) > 8:
        console.print(f"  … ещё {len(to_apply) - 8}")

    if not Confirm.ask("Обновить accounts.json?", default=True):
        return

    for name, aid, ahash, phone in to_apply:
        upsert_telethon_account(name, aid, ahash, phone=phone)
    console.print(f"[green]Готово: {accounts_json_path()}[/]")


async def run_telethon_session_menu(console) -> None:
    """Подменю: список / привязать .session / новая авторизация."""
    while True:
        console.print()
        console.print("[bold cyan]Сессии Telethon (.session)[/]")
        console.print(f"[[1]] Список: файлы в {_session_dir_label()}/ и accounts.json")
        console.print("[[2]] Привязать готовый .session (скопировать + запись в accounts.json)")
        console.print("[[3]] Новая авторизация по телефону (создать .session)")
        console.print("[[4]] Автопривязка: папка с .session или config/session_bind.txt")
        console.print("[[0]] Назад в главное меню")
        sub = Prompt.ask("Выбор", choices=["0", "1", "2", "3", "4"], default="0")
        if sub == "0":
            break
        if sub == "1":
            await _list_sessions_console(console)
        elif sub == "2":
            await _bind_session_console(console)
        elif sub == "3":
            await _new_login_console(console)
        elif sub == "4":
            await _auto_bind_sessions_console(console)
        Prompt.ask("\n[dim]Enter — продолжить[/]", default="")
