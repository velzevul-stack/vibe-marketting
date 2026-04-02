"""CLI-логика рассылки по CSV из пакета campaign (без интерактивного меню)."""
from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

from rich.console import Console


def parse_csv_minutes_interval(spec: str, *, flag: str) -> tuple[float, float | None]:
    """
    Одно число минут или ``MIN-MAX`` (случайный uniform между отправками).
    Допускается дефис ``-`` и длинное тире.
    """
    s = spec.strip().replace("—", "-").replace("–", "-")
    if not s:
        raise ValueError(f"{flag}: пустое значение")
    if "-" in s:
        a, b = s.split("-", 1)
        lo, hi = float(a.strip()), float(b.strip())
        if hi < lo:
            raise ValueError(f"{flag}: max меньше min")
        return (lo, hi)
    return (float(s), None)


def run_csv_broadcast_cli(
    dir_path: str,
    csv_path: str,
    delay_minutes_spec: str,
    zip_conflict: str,
    *,
    csv_limit: int | None,
    send_media: bool = True,
    sent_log: str | None = None,
    skip_sent: bool = False,
    csv_account_gap_spec: str | None = None,
) -> int:
    """Рассылка по CSV из пакета: sessions_bind, apis_sessions, proxy.txt RR, без БД."""
    from src.account_zip_import import import_sessions_zip, print_zip_import_report
    from src.broadcast.bundle import (
        discover_campaign_import_slices,
        load_campaign_bundle,
        validate_campaign_bundle,
        validate_extra_import_slices,
    )
    from src.broadcast.campaign_assign import apply_package_api_proxy_assignments
    from src.broadcast.csv_runner import (
        load_csv_recipients,
        load_sent_user_ids_from_jsonl,
        run_csv_dm_broadcast,
    )
    from src.broadcast.ignore_list import load_username_ignore_set
    from src.config import (
        Settings,
        account_session_has_full_api,
        account_session_has_proxy,
    )

    con = Console()
    root = Path(dir_path).expanduser().resolve()
    repo_out = Path(__file__).resolve().parent.parent.parent / "output"
    default_log = repo_out / "csv_broadcast_sent.jsonl"
    log_path = (
        Path(sent_log).expanduser().resolve()
        if sent_log
        else default_log
    )

    bundle = load_campaign_bundle(root)
    slices = discover_campaign_import_slices(root)
    errs = validate_campaign_bundle(bundle, require_images=send_media)
    errs.extend(validate_extra_import_slices(slices))
    if errs:
        for e in errs:
            con.print(f"[red]{e}[/]")
        return 1

    sett = Settings()
    stem_to_apis_file: dict[str, Path] = {}
    stem_to_proxy_file: dict[str, Path] = {}
    all_imported_order: list[str] = []

    def _apply_slice_csv(sl) -> int:
        if not sl.zip_path.is_file():
            con.print(f"[yellow]Пропуск слайса {sl.label}: нет {sl.zip_path.name}[/]")
            return 0
        try:
            rep = import_sessions_zip(sl.zip_path, on_conflict=zip_conflict, settings=sett)
        except (OSError, zipfile.BadZipFile) as e:
            con.print(f"[red]ZIP {sl.zip_path.name}: {e}[/]")
            return 1
        con.print(f"[bold]{sl.label}[/] — {sl.zip_path.name}")
        print_zip_import_report(con, rep)
        for stem in rep.imported_stems:
            stem_to_apis_file[stem] = sl.apis_file
            stem_to_proxy_file[stem] = sl.proxies_file
            all_imported_order.append(stem)
        return 0

    for sl in slices:
        if _apply_slice_csv(sl):
            return 1

    unique_imported = list(dict.fromkeys(all_imported_order))
    if not unique_imported:
        con.print(
            "[red]Не импортировано ни одной новой сессии из ZIP "
            "(все конфликты skip или архивы пусты).[/]"
        )
        return 1

    if not apply_package_api_proxy_assignments(
        console=con,
        sett=sett,
        root=root,
        unique_imported=unique_imported,
        stem_to_apis_file=stem_to_apis_file,
        stem_to_proxy_file=stem_to_proxy_file,
    ):
        return 1

    active_sessions = sorted(
        s
        for s in unique_imported
        if account_session_has_full_api(s) and account_session_has_proxy(s)
    )
    if not active_sessions:
        con.print("[red]После назначения API нет ни одной активной импортированной сессии.[/]")
        return 1

    skip_ids = load_sent_user_ids_from_jsonl(log_path) if skip_sent else frozenset()
    csv_p = Path(csv_path).expanduser().resolve()
    _ign_users = load_username_ignore_set(campaign_root=root)
    loaded = load_csv_recipients(
        csv_p,
        limit=csv_limit,
        skip_user_ids=skip_ids,
        ignore_usernames=_ign_users,
    )
    if loaded.skipped_ignore_list:
        con.print(
            f"[dim]ignor_list:[/] пропущено строк CSV: [yellow]{loaded.skipped_ignore_list}[/]"
        )
    for w in loaded.warnings[:30]:
        con.print(f"[yellow]{w}[/]")
    if not loaded.users:
        con.print("[red]Нет получателей в CSV (после лимита и --csv-skip-sent).[/]")
        return 1

    try:
        d_lo, d_hi = parse_csv_minutes_interval(
            delay_minutes_spec, flag="--csv-delay-minutes"
        )
    except ValueError as e:
        con.print(f"[red]{e}[/]")
        return 1
    delay_sec = max(0.0, d_lo * 60.0)
    delay_max_sec = None if d_hi is None else max(delay_sec, d_hi * 60.0)

    if csv_account_gap_spec is None:
        csv_gap_sec = max(0.0, float(sett.broadcast_min_gap_between_accounts_sec))
        csv_gap_max_sec = None
    else:
        try:
            g_lo, g_hi = parse_csv_minutes_interval(
                csv_account_gap_spec, flag="--csv-account-gap-minutes"
            )
        except ValueError as e:
            con.print(f"[red]{e}[/]")
            return 1
        if g_lo <= 0 and (g_hi is None or g_hi <= 0):
            csv_gap_sec = 0.0
            csv_gap_max_sec = None
        else:
            csv_gap_sec = max(0.0, g_lo * 60.0)
            csv_gap_max_sec = None if g_hi is None else max(csv_gap_sec, g_hi * 60.0)

    async def _run():
        return await run_csv_dm_broadcast(
            bundle=bundle,
            settings=sett,
            console=con,
            recipients=loaded.users,
            session_names=active_sessions,
            delay_seconds=delay_sec,
            send_media=send_media,
            sent_log_path=log_path,
            account_gap_seconds=csv_gap_sec,
            delay_max_seconds=delay_max_sec,
            account_gap_max_seconds=csv_gap_max_sec,
        )

    totals = asyncio.run(_run())
    con.print(
        f"[bold]CSV рассылка завершена:[/] ok={totals.sent} fail={totals.failed} skip={totals.skipped} "
        f"[dim]лог:[/] {log_path}"
    )
    return 0
