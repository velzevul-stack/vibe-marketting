"""Общая логика назначения API/прокси после импорта ZIP пакета campaign."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from src.broadcast.bundle import (
    APIS_SESSIONS_FILENAME,
    SESSIONS_BIND_FILENAME,
    parse_apis_sessions_file,
    parse_sessions_bind_file,
)
from src.config import (
    Settings,
    account_session_has_full_api,
    account_session_has_proxy,
    assign_apis_explicit_to_stems,
    assign_apis_round_robin_to_accounts,
    assign_proxies_round_robin_to_accounts,
    assign_sessions_bind_to_accounts,
    load_api_pairs_from_file,
    load_proxy_pool_from_file,
)


def apply_package_api_proxy_assignments(
    *,
    console: Console,
    sett: Settings,
    root: Path,
    unique_imported: list[str],
    stem_to_apis_file: dict[str, Path],
    stem_to_proxy_file: dict[str, Path],
) -> bool:
    """
    Порядок: ``sessions_bind.txt`` (api+proxy+сессия[+телефон]) → ``apis_sessions.txt`` (только api, без стемов из bind)
    → RR ``apis.txt`` для импортированных без api → RR ``proxy.txt``/``proxies.txt`` для импортированных без прокси.
    """
    bind_path = root / SESSIONS_BIND_FILENAME
    bind_rows, bind_errs = parse_sessions_bind_file(bind_path)
    for e in bind_errs:
        console.print(f"[red]{escape(e)}[/]")
    if bind_errs:
        return False
    bind_stems = {stem for _, _, stem, _ in bind_rows}
    if bind_rows:
        ok_b, msg_b = assign_sessions_bind_to_accounts(bind_rows, sett)
        if not ok_b:
            console.print(f"[red]{escape(msg_b)}[/]")
            return False
        console.print(f"[dim]Привязка ({SESSIONS_BIND_FILENAME}):[/] {escape(msg_b)}")

    map_path = root / APIS_SESSIONS_FILENAME
    api_only, api_errs = parse_apis_sessions_file(map_path)
    for e in api_errs:
        console.print(f"[red]{escape(e)}[/]")
    if api_errs:
        return False
    api_filtered = {k: v for k, v in api_only.items() if k not in bind_stems}
    skipped_api_sess = len(api_only) - len(api_filtered)
    if skipped_api_sess:
        console.print(
            f"[dim]apis_sessions.txt:[/] пропущено стемов уже заданных в "
            f"{SESSIONS_BIND_FILENAME}: [yellow]{skipped_api_sess}[/]"
        )
    if api_filtered:
        ok_e, msg_e = assign_apis_explicit_to_stems(api_filtered, sett)
        if not ok_e:
            console.print(f"[red]{escape(msg_e)}[/]")
            return False
        console.print(f"[dim]Явное API ({APIS_SESSIONS_FILENAME}):[/] {escape(msg_e)}")

    need_api_by_file: dict[Path, list[str]] = defaultdict(list)
    for stem in unique_imported:
        if not account_session_has_full_api(stem):
            ap = stem_to_apis_file.get(stem)
            if ap is None:
                console.print(f"[red]Стем {stem!r} без пути к apis.txt слайса[/]")
                return False
            need_api_by_file[ap].append(stem)
    for ap_path, stems in need_api_by_file.items():
        pairs = load_api_pairs_from_file(ap_path)
        if not pairs:
            console.print(
                f"[red]Для стемов {stems!r} нужен RR API, пусто {escape(ap_path.name)}[/]"
            )
            return False
        ok_rr, msg_rr = assign_apis_round_robin_to_accounts(
            pairs, sett, only_session_names=frozenset(stems)
        )
        if not ok_rr:
            console.print(f"[red]{escape(msg_rr)}[/]")
            return False
        console.print(f"[dim]API RR ({escape(ap_path.name)}):[/] {escape(msg_rr)}")

    need_px_by_file: dict[Path, list[str]] = defaultdict(list)
    for stem in unique_imported:
        if not account_session_has_proxy(stem):
            px = stem_to_proxy_file.get(stem)
            if px is None:
                console.print(f"[red]Стем {stem!r} без пути к proxy.txt слайса[/]")
                return False
            need_px_by_file[px].append(stem)
    for px_path, stems in need_px_by_file.items():
        pool = load_proxy_pool_from_file(px_path)
        if not pool:
            console.print(
                f"[red]Для стемов {stems!r} нужен RR прокси, пусто {escape(px_path.name)}[/]"
            )
            return False
        ok_p, msg_p = assign_proxies_round_robin_to_accounts(
            sett, proxy_pool=pool, only_session_names=frozenset(stems)
        )
        if not ok_p:
            console.print(f"[red]{escape(msg_p)}[/]")
            return False
        console.print(f"[dim]Прокси RR ({escape(px_path.name)}):[/] {escape(msg_p)}")

    return True
