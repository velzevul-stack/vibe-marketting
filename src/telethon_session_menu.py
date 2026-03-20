"""Консоль: сессии Telethon (.session) и accounts.json."""
import shutil
from pathlib import Path

from rich.prompt import Prompt, Confirm
from rich.table import Table

from src.config import (
    accounts_json_path,
    effective_2fa_password,
    load_accounts,
    load_accounts_all,
    save_accounts_all,
    telethon_session_dir_path,
)

def _sessions_dir() -> Path:
    return telethon_session_dir_path()


def _session_paths() -> list[Path]:
    d = _sessions_dir()
    if not d.is_dir():
        return []
    return sorted(d.glob("*.session"))


def _is_telethon_account(row: dict) -> bool:
    return bool(
        row.get("api_id")
        and row.get("api_hash")
        and not row.get("_template")
    )


def _append_account(
    session_name: str,
    api_id: int,
    api_hash: str,
    phone: str | None = None,
    proxy: str | None = None,
) -> None:
    rows = load_accounts_all()
    # убрать старую запись с тем же session_name
    rows = [r for r in rows if not (_is_telethon_account(r) and r.get("session_name") == session_name)]
    entry: dict = {
        "session_name": session_name,
        "api_id": api_id,
        "api_hash": api_hash.strip(),
    }
    if phone:
        entry["phone"] = phone.strip()
    if proxy and proxy.strip():
        entry["proxy"] = proxy.strip()
    rows.append(entry)
    save_accounts_all(rows)


async def _list_sessions_console(console) -> None:
    files = {p.stem for p in _session_paths()}
    accs = load_accounts()
    table = Table(title="Сессии Telethon")
    table.add_column("session_name", style="cyan")
    table.add_column("Файл sessions/*.session", style="green")
    table.add_column("В accounts.json", style="yellow")
    table.add_column("phone", style="dim")
    names = sorted(files | {a.get("session_name", "") for a in accs if a.get("session_name")})
    names = [n for n in names if n]
    if not names:
        console.print("[yellow]Нет .session в sessions/ и нет аккаунтов в accounts.json[/]")
        return
    by_name = {a.get("session_name"): a for a in accs}
    for name in names:
        has_file = "да" if name in files else "нет"
        in_json = "да" if name in by_name else "нет"
        phone = (by_name.get(name) or {}).get("phone") or "—"
        table.add_row(name, has_file, in_json, str(phone))
    console.print(table)
    console.print(
        "[dim]Telethon ищет файл sessions/<session_name>.session. "
        "В accounts.json нужны api_id и api_hash с https://my.telegram.org "
        "(те же, что при создании сессии).[/]"
    )


async def _bind_session_console(console) -> None:
    raw = Prompt.ask(
        "Путь к .session или имя без расширения (если файл уже в sessions/)",
    ).strip()
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
            console.print(f"[red]Нет файла {dest}. Положите .session в папку sessions/ или укажите полный путь.[/]")
            return

    api_id_s = Prompt.ask("api_id (число с my.telegram.org)").strip()
    api_hash = Prompt.ask("api_hash").strip()
    try:
        api_id = int(api_id_s)
    except ValueError:
        console.print("[red]api_id должен быть числом[/]")
        return
    phone = Prompt.ask("Телефон (опционально)", default="").strip() or None
    proxy = Prompt.ask("Прокси URL (опционально)", default="").strip() or None

    if Confirm.ask(f"Добавить/обновить аккаунт «{session_name}» в accounts.json?", default=True):
        _append_account(session_name, api_id, api_hash, phone=phone, proxy=proxy)
        console.print(f"[green]Готово: {accounts_json_path()}[/]")


async def _new_login_console(console) -> None:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    session_name = Prompt.ask("Имя сессии (будет sessions/ИМЯ.session)").strip()
    if not session_name or "/" in session_name or "\\" in session_name:
        console.print("[red]Некорректное имя[/]")
        return
    api_id_s = Prompt.ask("api_id").strip()
    try:
        api_id = int(api_id_s)
    except ValueError:
        console.print("[red]api_id должен быть числом[/]")
        return
    api_hash = Prompt.ask("api_hash").strip()
    phone = Prompt.ask("Телефон в формате +375...").strip()
    if not phone:
        console.print("[red]Нужен телефон[/]")
        return

    _sessions_dir().mkdir(parents=True, exist_ok=True)
    session_base = str(_sessions_dir() / session_name)

    def code_cb() -> str:
        return Prompt.ask("Код из Telegram (SMS или приложение)")

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

    proxy = Prompt.ask("Прокси URL (опционально)", default="").strip() or None
    if Confirm.ask("Записать аккаунт в accounts.json?", default=True):
        _append_account(session_name, api_id, api_hash, phone=phone, proxy=proxy)
        console.print(f"[green]Сохранено в {accounts_json_path()}[/]")


async def run_telethon_session_menu(console) -> None:
    """Подменю: список / привязать .session / новая авторизация."""
    while True:
        console.print()
        console.print("[bold cyan]Сессии Telethon (.session)[/]")
        console.print("[1] Список: файлы в sessions/ и accounts.json")
        console.print("[2] Привязать готовый .session (скопировать + запись в accounts.json)")
        console.print("[3] Новая авторизация по телефону (создать .session)")
        console.print("[0] Назад в главное меню")
        sub = Prompt.ask("Выбор", choices=["0", "1", "2", "3"], default="0")
        if sub == "0":
            break
        if sub == "1":
            await _list_sessions_console(console)
        elif sub == "2":
            await _bind_session_console(console)
        elif sub == "3":
            await _new_login_console(console)
        Prompt.ask("\n[dim]Enter — продолжить[/]", default="")
