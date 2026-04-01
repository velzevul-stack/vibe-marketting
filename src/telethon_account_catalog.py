"""Каталог Telethon-аккаунтов: стабильные display_id и выбор подмножества."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt
from rich.table import Table

from src.cli_input import strip_c0_controls
from src.config import load_accounts


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
    """Краткая строка для таблицы: last_used_* или legacy last_broadcast / last_mytg."""
    lu = row.get("last_used_at")
    lk = row.get("last_used_kind")
    if isinstance(lu, str) and lu.strip():
        kind = (lk if isinstance(lk, str) else "") or "?"
        return f"{kind} {lu.strip()[:19]}"
    lb = row.get("last_broadcast_at")
    lm = row.get("last_mytg_at")
    bits: list[str] = []
    if isinstance(lb, str) and lb.strip():
        bits.append(f"рассылка {lb.strip()[:19]}")
    if isinstance(lm, str) and lm.strip():
        bits.append(f"API {lm.strip()[:19]}")
    return " · ".join(bits) if bits else "—"


def mask_phone(phone: str | None) -> str:
    if not phone or not str(phone).strip():
        return "—"
    p = str(phone).strip()
    if len(p) <= 4:
        return "****"
    return p[:3] + "…" + p[-2:]


def print_account_catalog_table(console: Console, catalog: list[TelethonCatalogEntry]) -> None:
    """Rich-таблица: id, session_name, phone, последнее использование."""
    table = Table(title="Аккаунты (Telethon)")
    table.add_column("id", justify="right", style="cyan")
    table.add_column("session_name", style="bold")
    table.add_column("phone", style="dim")
    table.add_column("последнее использование (UTC)", style="dim")

    sn_counts: dict[str, int] = {}
    for e in catalog:
        sn_counts[e.session_name] = sn_counts.get(e.session_name, 0) + 1

    for e in catalog:
        warn = " ⚠дубль" if sn_counts.get(e.session_name, 0) > 1 else ""
        table.add_row(
            str(e.display_id),
            escape(e.session_name) + warn,
            escape(mask_phone(e.row.get("phone"))),
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
