"""Каталог Telethon-аккаунтов: стабильные display_id и выбор подмножества."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from src.cli_input import strip_c0_controls
from src.config import is_placeholder_proxy_url, load_accounts

_MSK = ZoneInfo("Europe/Moscow")


def _parse_iso_datetime(s: str) -> datetime | None:
    t = (s or "").strip()
    if len(t) < 10:
        return None
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_ts_moscow(raw: str) -> str:
    dt = _parse_iso_datetime(raw)
    if dt:
        return dt.astimezone(_MSK).strftime("%Y-%m-%d %H:%M МСК")
    return ""


def row_has_assigned_proxy(row: dict) -> bool:
    p = row.get("proxy")
    if not isinstance(p, str) or not p.strip():
        return False
    return not is_placeholder_proxy_url(p)


def row_has_api_credentials(row: dict) -> bool:
    aid = row.get("api_id")
    ah = row.get("api_hash")
    if aid is None or ah is None:
        return False
    if str(aid).strip() == "" or str(ah).strip() == "":
        return False
    try:
        if int(aid) == 0:
            return False
    except (TypeError, ValueError):
        return False
    return True


def _yes_no_cell(yes: bool) -> Text:
    return Text("да", style="green") if yes else Text("нет", style="dim")


@dataclass(frozen=True)
class TelethonCatalogEntry:
    """Одна строка каталога (порядок = порядок в load_accounts())."""

    display_id: int
    session_name: str
    row: dict


def build_telethon_catalog() -> list[TelethonCatalogEntry]:
    """Список аккаунтов с api_id+api_hash; id 1..N по порядку в accounts.json."""
    accs = load_accounts()
    out: list[TelethonCatalogEntry] = []
    for i, row in enumerate(accs, start=1):
        sn = (row.get("session_name") or "").strip()
        if not sn:
            sn = "?"
        out.append(TelethonCatalogEntry(display_id=i, session_name=sn, row=row))
    return out


def catalog_all_session_names(catalog: list[TelethonCatalogEntry]) -> frozenset[str]:
    return frozenset(e.session_name for e in catalog if e.session_name and e.session_name != "?")


def parse_account_ids_csv(raw: str, *, n_max: int) -> frozenset[int]:
    """
    Разбор строки вида ``1, 2, 5``. Пусто → пустое множество.
    Каждое число в 1..n_max; дубликаты убираются.
    """
    s = strip_c0_controls(raw or "").strip()
    if not s:
        return frozenset()
    parts = re.split(r"[,;\s]+", s)
    ids: set[int] = set()
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not p.isdigit():
            raise ValueError(f"не число: {p!r}")
        v = int(p)
        if v < 1 or v > n_max:
            raise ValueError(f"id вне диапазона 1–{n_max}: {v}")
        ids.add(v)
    return frozenset(ids)


def ids_to_session_names(
    catalog: list[TelethonCatalogEntry], ids: frozenset[int]
) -> frozenset[str]:
    by_id = {e.display_id: e for e in catalog}
    return frozenset(by_id[i].session_name for i in ids if i in by_id)


def format_last_use_line(row: dict) -> str:
    """Краткая строка для таблицы: last_used_* или legacy last_broadcast / last_mytg (время — МСК)."""
    lu = row.get("last_used_at")
    lk = row.get("last_used_kind")
    if isinstance(lu, str) and lu.strip():
        kind = (lk if isinstance(lk, str) else "") or "?"
        ts_disp = _format_ts_moscow(lu.strip())
        if ts_disp:
            return f"{kind} {ts_disp}"
        return f"{kind} {lu.strip()[:22]}"
    lb = row.get("last_broadcast_at")
    lm = row.get("last_mytg_at")
    bits: list[str] = []
    if isinstance(lb, str) and lb.strip():
        d = _format_ts_moscow(lb.strip()) or lb.strip()[:19]
        bits.append(f"рассылка {d}")
    if isinstance(lm, str) and lm.strip():
        d = _format_ts_moscow(lm.strip()) or lm.strip()[:19]
        bits.append(f"mytg {d}")
    return " · ".join(bits) if bits else "—"


def mask_phone(phone: str | None) -> str:
    if not phone or not str(phone).strip():
        return "—"
    p = str(phone).strip()
    if len(p) <= 4:
        return "****"
    return p[:3] + "…" + p[-2:]


def print_account_catalog_table(console: Console, catalog: list[TelethonCatalogEntry]) -> None:
    """Rich-таблица: id, session_name, phone, прокси/API, последнее использование (МСК)."""
    table = Table(title="Аккаунты (Telethon)")
    table.add_column("id", justify="right", style="cyan")
    table.add_column("session_name", style="bold")
    table.add_column("phone", style="dim")
    table.add_column("прокси", justify="center")
    table.add_column("API", justify="center")
    table.add_column("последнее использование (МСК)", style="dim")

    sn_counts: dict[str, int] = {}
    for e in catalog:
        sn_counts[e.session_name] = sn_counts.get(e.session_name, 0) + 1

    for e in catalog:
        warn = " ⚠дубль" if sn_counts.get(e.session_name, 0) > 1 else ""
        table.add_row(
            str(e.display_id),
            escape(e.session_name) + warn,
            escape(mask_phone(e.row.get("phone"))),
            _yes_no_cell(row_has_assigned_proxy(e.row)),
            _yes_no_cell(row_has_api_credentials(e.row)),
            escape(format_last_use_line(e.row)),
        )
    console.print(table)


AccountScopeMode = Literal["all", "subset"]


def prompt_account_scope(
    console: Console,
    catalog: list[TelethonCatalogEntry],
    *,
    title: str = "Аккаунты для этой операции",
) -> tuple[AccountScopeMode, frozenset[str]]:
    """
    Выбор: все аккаунты каталога или подмножество по id (через запятую).
    Возвращает (режим, frozenset session_name); для «все» — явный полный набор.
    """
    if not catalog:
        console.print("[yellow]Нет аккаунтов в accounts.json (api_id+api_hash).[/]")
        return "all", frozenset()

    all_names = catalog_all_session_names(catalog)
    console.print(f"[bold]{escape(title)}[/]")
    console.print("  [cyan]1[/]  Все аккаунты")
    console.print("  [cyan]2[/]  Только выбранные по id (как в таблице сводки, через запятую)")
    ch = Prompt.ask("Выбор", choices=["1", "2"], default="1")
    if ch == "1":
        return "all", all_names

    n_max = len(catalog)
    while True:
        raw = strip_c0_controls(
            Prompt.ask(
                f"id через запятую (1–{n_max}, например 1,3,5)",
                default="1",
            ).strip()
        )
        try:
            ids = parse_account_ids_csv(raw, n_max=n_max)
        except ValueError as e:
            console.print(f"[red]{escape(str(e))}[/]")
            continue
        if not ids:
            console.print("[red]Нужен хотя бы один id.[/]")
            continue
        names = ids_to_session_names(catalog, ids)
        if not names:
            console.print("[red]Не удалось сопоставить id с аккаунтами.[/]")
            continue
        return "subset", names


def prompt_account_scope_optional_single(
    console: Console,
    catalog: list[TelethonCatalogEntry],
    *,
    title: str,
) -> tuple[AccountScopeMode, frozenset[str]] | None:
    """
    Как prompt_account_scope, но если каталог пуст — возвращает None.
    """
    if not catalog:
        return None
    return prompt_account_scope(console, catalog, title=title)


def prompt_row_indices_scope(
    console: Console,
    n_rows: int,
    *,
    title: str = "Какие строки включить в прогон",
) -> frozenset[int]:
    """
    Выбор подмножества строк по номерам 1..n_rows (как в показанной таблице).
    Возвращает множество 1-based индексов.
    """
    if n_rows <= 0:
        return frozenset()
    console.print(f"[bold]{escape(title)}[/]")
    console.print("  [cyan]1[/]  Все строки")
    console.print(
        f"  [cyan]2[/]  Только выбранные номера [dim](1–{n_rows}, через запятую)[/]"
    )
    ch = Prompt.ask("Выбор", choices=["1", "2"], default="1")
    if ch == "1":
        return frozenset(range(1, n_rows + 1))
    while True:
        raw = strip_c0_controls(
            Prompt.ask(
                f"Номера через запятую (1–{n_rows})",
                default="1",
            ).strip()
        )
        try:
            ids = parse_account_ids_csv(raw, n_max=n_rows)
        except ValueError as e:
            console.print(f"[red]{escape(str(e))}[/]")
            continue
        if not ids:
            console.print("[red]Нужен хотя бы один номер.[/]")
            continue
        return ids
