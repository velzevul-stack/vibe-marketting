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


def _cli_broadcast(
    dir_path: str,
    limit: int,
    category: str,
    zip_conflict: str,
    broadcast_mode: str,
    *,
    send_media: bool = True,
) -> int:
    """Рассылка из каталога-пакета без меню (ZIP, apis.txt, proxies.txt, рассылка)."""
    import asyncio
    import zipfile

    from rich.console import Console

    from src.account_zip_import import import_sessions_zip, print_zip_import_report
    from src.broadcast.bundle import (
        discover_campaign_import_slices,
        load_campaign_bundle,
        validate_campaign_bundle,
        validate_extra_import_slices,
    )
    from src.broadcast.runner import run_dm_broadcast
    from src.config import (
        Settings,
        assign_apis_round_robin_to_accounts,
        assign_proxies_round_robin_to_accounts,
        load_api_pairs_from_file,
        load_proxy_pool_from_file,
    )
    from src.db import get_db

    con = Console()
    root = Path(dir_path).expanduser().resolve()
    bundle = load_campaign_bundle(root)
    slices = discover_campaign_import_slices(root)
    errs = validate_campaign_bundle(bundle, require_images=send_media)
    errs.extend(validate_extra_import_slices(slices))
    if errs:
        for e in errs:
            con.print(f"[red]{e}[/]")
        return 1

    sett = Settings()

    def _apply_slice(sl) -> int:
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
        stems = frozenset(rep.imported_stems)
        api_pairs = load_api_pairs_from_file(sl.apis_file)
        if not api_pairs:
            con.print(f"[red]В {sl.apis_file.name} нет валидных пар api_id:api_hash.[/]")
            return 1
        if stems:
            ok_api, msg_api = assign_apis_round_robin_to_accounts(
                api_pairs, sett, only_session_names=stems
            )
        elif sl.label == "accounts":
            ok_api, msg_api = assign_apis_round_robin_to_accounts(api_pairs, sett)
        else:
            con.print(
                f"[yellow]{sl.label}: в ZIP не скопировано новых сессий — API/прокси для этого слайса не трогаем.[/]"
            )
            return 0
        if not ok_api:
            con.print(f"[red]{msg_api}[/]")
            return 1
        con.print(f"[dim]API ({sl.label}):[/] {msg_api}")

        proxies = load_proxy_pool_from_file(sl.proxies_file)
        if not proxies:
            con.print(f"[red]В {sl.proxies_file.name} нет валидных прокси.[/]")
            return 1
        if stems:
            ok_px, msg_px = assign_proxies_round_robin_to_accounts(
                sett, proxy_pool=proxies, only_session_names=stems
            )
        elif sl.label == "accounts":
            ok_px, msg_px = assign_proxies_round_robin_to_accounts(sett, proxy_pool=proxies)
        else:
            return 0
        if not ok_px:
            con.print(f"[red]{msg_px}[/]")
            return 1
        con.print(f"[dim]Прокси ({sl.label}):[/] {msg_px}")
        return 0

    for sl in slices:
        if _apply_slice(sl):
            return 1

    cat_val = None if category == "all" else category
    mode = "privacy_retry" if broadcast_mode == "privacy_retry" else "normal"

    async def _run():
        return await run_dm_broadcast(
            bundle=bundle,
            db=get_db(),
            settings=sett,
            console=con,
            category=cat_val,
            total_limit=limit,
            exclude_invited=True,
            broadcast_mode=mode,
            send_media=send_media,
        )

    totals = asyncio.run(_run())
    suf = (
        f" username_not_found_marked={totals.username_not_found_marked}"
        if totals.username_not_found_marked
        else ""
    )
    con.print(
        f"[bold]Рассылка завершена:[/] ok={totals.sent} fail={totals.failed} "
        f"skip={totals.skipped} privacy={totals.privacy_skipped} daily_cap={totals.deferred_daily_cap} "
        f"relay_exhausted={totals.relay_exhausted}{suf}"
    )
    return 0


def _cli_csv_broadcast(
    dir_path: str,
    csv_path: str,
    delay_minutes: float,
    zip_conflict: str,
    *,
    csv_limit: int | None,
    send_media: bool = True,
    sent_log: str | None = None,
    skip_sent: bool = False,
) -> int:
    """Рассылка по CSV из пакета: apis_sessions.txt (явно) + apis.txt (RR), без БД, jsonl-лог."""
    import asyncio
    import zipfile
    from collections import defaultdict

    from rich.console import Console

    from src.account_zip_import import import_sessions_zip, print_zip_import_report
    from src.broadcast.bundle import (
        APIS_SESSIONS_FILENAME,
        discover_campaign_import_slices,
        load_campaign_bundle,
        parse_apis_sessions_file,
        validate_campaign_bundle,
        validate_extra_import_slices,
    )
    from src.broadcast.csv_runner import (
        load_csv_recipients,
        load_sent_user_ids_from_jsonl,
        run_csv_dm_broadcast,
    )
    from src.config import (
        Settings,
        account_session_has_full_api,
        assign_apis_explicit_to_stems,
        assign_apis_round_robin_to_accounts,
        assign_proxies_round_robin_to_accounts,
        load_api_pairs_from_file,
        load_proxy_pool_from_file,
    )

    con = Console()
    root = Path(dir_path).expanduser().resolve()
    repo_out = Path(__file__).resolve().parent / "output"
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
            all_imported_order.append(stem)
        stems = frozenset(rep.imported_stems)
        proxies = load_proxy_pool_from_file(sl.proxies_file)
        if not proxies:
            con.print(f"[red]В {sl.proxies_file.name} нет валидных прокси.[/]")
            return 1
        if stems:
            ok_px, msg_px = assign_proxies_round_robin_to_accounts(
                sett, proxy_pool=proxies, only_session_names=stems
            )
        elif sl.label == "accounts":
            ok_px, msg_px = assign_proxies_round_robin_to_accounts(sett, proxy_pool=proxies)
        else:
            con.print(
                f"[yellow]{sl.label}: в ZIP не скопировано новых сессий — прокси для слайса не трогаем.[/]"
            )
            return 0
        if not ok_px:
            con.print(f"[red]{msg_px}[/]")
            return 1
        con.print(f"[dim]Прокси ({sl.label}):[/] {msg_px}")
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

    map_path = root / APIS_SESSIONS_FILENAME
    mapping, map_errs = parse_apis_sessions_file(map_path)
    for e in map_errs:
        con.print(f"[red]{e}[/]")
    if map_errs:
        return 1

    if mapping:
        ok_exp, msg_exp = assign_apis_explicit_to_stems(mapping, sett)
        if not ok_exp:
            con.print(f"[red]{msg_exp}[/]")
            return 1
        con.print(f"[dim]Явное API ({APIS_SESSIONS_FILENAME}):[/] {msg_exp}")

    need_rr_by_file: dict[Path, list[str]] = defaultdict(list)
    for stem in unique_imported:
        if not account_session_has_full_api(stem):
            ap = stem_to_apis_file.get(stem) or bundle.apis_file
            need_rr_by_file[ap].append(stem)

    for ap_path, stems in need_rr_by_file.items():
        pairs = load_api_pairs_from_file(ap_path)
        if not pairs:
            con.print(
                f"[red]Для стемов {stems!r} нужен round-robin API, "
                f"но в {ap_path.name} нет валидных пар api_id:api_hash.[/]"
            )
            return 1
        ok_rr, msg_rr = assign_apis_round_robin_to_accounts(
            pairs, sett, only_session_names=frozenset(stems)
        )
        if not ok_rr:
            con.print(f"[red]{msg_rr}[/]")
            return 1
        con.print(f"[dim]API RR ({ap_path.name}):[/] {msg_rr}")

    active_sessions = sorted(
        {s for s in unique_imported if account_session_has_full_api(s)}
    )
    if not active_sessions:
        con.print("[red]После назначения API нет ни одной активной импортированной сессии.[/]")
        return 1

    skip_ids = load_sent_user_ids_from_jsonl(log_path) if skip_sent else frozenset()
    csv_p = Path(csv_path).expanduser().resolve()
    loaded = load_csv_recipients(csv_p, limit=csv_limit, skip_user_ids=skip_ids)
    for w in loaded.warnings[:30]:
        con.print(f"[yellow]{w}[/]")
    if not loaded.users:
        con.print("[red]Нет получателей в CSV (после лимита и --csv-skip-sent).[/]")
        return 1

    delay_sec = max(0.0, float(delay_minutes) * 60.0)

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
        )

    totals = asyncio.run(_run())
    con.print(
        f"[bold]CSV рассылка завершена:[/] ok={totals.sent} fail={totals.failed} skip={totals.skipped} "
        f"[dim]лог:[/] {log_path}"
    )
    return 0


def _cli_cleanup(*, clear_accounts: bool, wipe_sessions: bool, clear_proxies: bool) -> int:
    from rich.console import Console
    from src.config import (
        Settings,
        clear_accounts_json,
        clear_proxy_pool_in_config,
        wipe_telethon_session_files,
    )

    con = Console()
    if clear_accounts:
        clear_accounts_json()
        con.print("[green]accounts.json очищен[/]")
    if wipe_sessions:
        n = wipe_telethon_session_files(Settings())
        con.print(f"[green]Удалено *.session:[/] {n}")
    if clear_proxies:
        ok, msg = clear_proxy_pool_in_config()
        con.print(f"[green]Прокси:[/] {msg}" if ok else f"[red]{msg}[/]")
    if not (clear_accounts or wipe_sessions or clear_proxies):
        con.print("[yellow]Укажите --clear-accounts, --wipe-sessions и/или --clear-proxies[/]")
        return 1
    return 0


def _cli_strip_account_apis(*, clear_default_api: bool) -> int:
    """Снять api_id/api_hash с аккаунтов и sidecar; опционально обнулить telethon_default_api."""
    from rich.console import Console
    from src.config import (
        Settings,
        clear_telethon_default_api,
        strip_api_credentials_from_accounts,
    )

    con = Console()
    n_acc, n_side, path = strip_api_credentials_from_accounts(Settings())
    con.print(
        f"[green]Сняты ключи приложения:[/] accounts.json записей={n_acc}, "
        f"sidecar обновлено={n_side} · [dim]{path}[/]"
    )
    if clear_default_api:
        ok, msg = clear_telethon_default_api()
        con.print(f"[green]telethon_default_api очищен:[/] [dim]{msg}[/]" if ok else f"[red]{msg}[/]")
        return 0 if ok else 1
    return 0


def _cli_assign_proxies_only() -> int:
    """Только перезаписать proxy в accounts.json из пула (без меню)."""
    from rich.console import Console
    from src.config import (
        assign_proxies_round_robin_to_accounts,
        bundle_round_robin_account_rows,
        load_accounts_all,
        load_proxy_pool_from_config,
    )

    con = Console()
    if not bundle_round_robin_account_rows(load_accounts_all()):
        con.print("[red]Нет строк с session_name в config/accounts.json[/]")
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
            "  python main.py --broadcast ./campaign --broadcast-limit 300\n"
            "  python main.py --csv-broadcast ./campaign --csv-recipients members.csv "
            "--csv-delay-minutes 30\n"
            "  python main.py --clear-accounts --wipe-sessions --clear-proxies\n"
            "  python main.py --strip-account-apis     снять api с accounts + sidecar (перед push)\n"
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
    parser.add_argument(
        "--broadcast",
        metavar="DIR",
        help="Рассылка из пакета (accounts.zip, apis.txt, proxies.txt, text_1/2.txt; фото — или --broadcast-text-only)",
    )
    parser.add_argument(
        "--broadcast-limit",
        type=int,
        default=500,
        help="Максимум пользователей из БД для рассылки (по умолчанию 500)",
    )
    parser.add_argument(
        "--broadcast-category",
        choices=["hot", "warm", "all"],
        default="hot",
        help="Категория строк в БД",
    )
    parser.add_argument(
        "--broadcast-zip-conflict",
        choices=["skip", "overwrite"],
        default="skip",
        help="Поведение при совпадении имён файлов при импорте ZIP",
    )
    parser.add_argument(
        "--broadcast-mode",
        choices=["normal", "privacy_retry"],
        default="normal",
        help="normal — обычная очередь; privacy_retry — только после UserPrivacyRestricted",
    )
    parser.add_argument(
        "--broadcast-text-only",
        action="store_true",
        help="Рассылка без вложений: только text_1/2.txt (без 1.jpg–3.jpg)",
    )
    parser.add_argument(
        "--csv-broadcast",
        metavar="DIR",
        help="Рассылка по CSV из пакета (ZIP, proxies, тексты; apis_sessions.txt + apis.txt)",
    )
    parser.add_argument(
        "--csv-recipients",
        metavar="FILE",
        help="CSV с колонками User ID, Username (вместе с --csv-broadcast)",
    )
    parser.add_argument(
        "--csv-delay-minutes",
        type=float,
        default=30.0,
        help="Пауза в минутах после логина до 1-го ЛС и между отправками (по умолчанию 30)",
    )
    parser.add_argument(
        "--csv-limit",
        type=int,
        default=None,
        help="Максимум строк из CSV (после дедупа и --csv-skip-sent)",
    )
    parser.add_argument(
        "--csv-sent-log",
        metavar="PATH",
        help="JSONL успешных отправок (по умолчанию output/csv_broadcast_sent.jsonl)",
    )
    parser.add_argument(
        "--csv-skip-sent",
        action="store_true",
        help="Не слать повторно user_id из JSONL (--csv-sent-log)",
    )
    parser.add_argument(
        "--csv-broadcast-text-only",
        action="store_true",
        help="Как --broadcast-text-only: только text_1/2.txt",
    )
    parser.add_argument(
        "--clear-accounts",
        action="store_true",
        help="Очистить config/accounts.json (пустой список)",
    )
    parser.add_argument(
        "--wipe-sessions",
        action="store_true",
        help="Удалить все *.session и sidecar *.json в каталоге сессий Telethon",
    )
    parser.add_argument(
        "--clear-proxies",
        action="store_true",
        help="Очистить config/proxies.txt и proxies.list в settings.json",
    )
    parser.add_argument(
        "--strip-account-apis",
        action="store_true",
        help="Удалить api_id/api_hash из accounts.json и sessions/*.json (без удаления сессий)",
    )
    parser.add_argument(
        "--strip-default-api",
        action="store_true",
        help="Вместе с --strip-account-apis: также обнулить telethon_default_api в settings.json",
    )
    args = parser.parse_args()
    if args.proxy is not None:
        raise SystemExit(_cli_proxy_state(args.proxy))
    if args.assign_proxies:
        _run_startup_session_sync()
        raise SystemExit(_cli_assign_proxies_only())
    if args.strip_account_apis:
        raise SystemExit(_cli_strip_account_apis(clear_default_api=args.strip_default_api))
    if args.strip_default_api:
        from rich.console import Console
        from src.config import clear_telethon_default_api

        con = Console()
        ok, msg = clear_telethon_default_api()
        con.print(f"[green]telethon_default_api очищен:[/] [dim]{msg}[/]" if ok else f"[red]{msg}[/]")
        raise SystemExit(0 if ok else 1)
    if args.broadcast:
        raise SystemExit(
            _cli_broadcast(
                args.broadcast,
                args.broadcast_limit,
                args.broadcast_category,
                args.broadcast_zip_conflict,
                args.broadcast_mode,
                send_media=not args.broadcast_text_only,
            )
        )
    if args.csv_broadcast or args.csv_recipients or args.csv_skip_sent:
        if not args.csv_broadcast or not args.csv_recipients:
            parser.error("--csv-broadcast и --csv-recipients задаются вместе")
        raise SystemExit(
            _cli_csv_broadcast(
                args.csv_broadcast,
                args.csv_recipients,
                args.csv_delay_minutes,
                args.broadcast_zip_conflict,
                csv_limit=args.csv_limit,
                send_media=not args.csv_broadcast_text_only,
                sent_log=args.csv_sent_log,
                skip_sent=args.csv_skip_sent,
            )
        )
    if args.clear_accounts or args.wipe_sessions or args.clear_proxies:
        raise SystemExit(
            _cli_cleanup(
                clear_accounts=args.clear_accounts,
                wipe_sessions=args.wipe_sessions,
                clear_proxies=args.clear_proxies,
            )
        )
    run_menu()


if __name__ == "__main__":
    main()
