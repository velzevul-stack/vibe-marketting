#!/usr/bin/env python3
"""
Очистка секретов и лишних файлов сессий Telethon (без трогания пакета рассылки).

Что трогается
  • config/accounts.json — снятие api_id/api_hash и proxy (по session_name).
  • Каталог telethon_session_dir (по умолчанию sessions/) — sidecar *.json, удаление сирот.
  • Опционально: config/proxies.txt, proxies.list и telethon_default_api в settings.json.

Что НЕ трогается
  • Каталог campaign/ (accounts.zip, apis.txt, proxies.txt пакета, text_*.txt, *.jpg) —
    скрипт к нему не обращается. Если в settings ошибочно указать telethon_session_dir
    внутри campaign/, скрипт завершится с ошибкой.

Как пользоваться (из корня репозитория)
  # только посмотреть сирот / строки без .session
  python scripts/clean_telegram_artifacts.py --dry-run --yes \\
      --delete-orphans --drop-missing-session-rows

  # полный сброс: секреты, пул прокси, default API, обрезка accounts.json, удаление сирот
  python scripts/clean_telegram_artifacts.py --yes --full-account-reset

  # только снять ключи с JSON, файлы сессий не удалять
  python scripts/clean_telegram_artifacts.py --yes

FAQ
  • Новые аккаунты не удалятся, если session_name есть в accounts.json и есть .session.
  • Если accounts.json пустой и включено удаление сирот — все *.session/*.json в папке
    сессий будут удалены; campaign/ при этом не затрагивается.
  • Перед git push обычно: --yes (или --full-account-reset), затем коммит без секретов.

Исходные флаги см. python scripts/clean_telegram_artifacts.py --help
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _session_dir_collides_with_campaign(session_dir: Path, campaign_root: Path) -> tuple[bool, str]:
    """
    True + сообщение, если telethon_session_dir совпадает с campaign или лежит внутри него.
    """
    try:
        sd = session_dir.resolve()
        cr = campaign_root.resolve()
    except OSError as e:
        return True, f"Не удалось разрешить путь: {e}"
    if sd == cr:
        return (
            True,
            "telethon_session_dir совпадает с campaign_dir — укажите отдельную папку для .session.",
        )
    try:
        sd.relative_to(cr)
        return (
            True,
            "telethon_session_dir находится внутри campaign_dir — очистка затронула бы пакет рассылки.",
        )
    except ValueError:
        pass
    return False, ""


def main() -> int:
    from rich.console import Console
    from rich.markup import escape

    from src.config import (
        Settings,
        clear_proxy_pool_in_config,
        clear_telethon_default_api,
        delete_orphan_session_artifacts,
        remove_account_rows_without_session_file,
        sanitize_all_session_sidecar_json_files,
        telethon_session_dir_path,
        strip_api_credentials_from_accounts,
        strip_proxy_from_accounts,
    )

    parser = argparse.ArgumentParser(
        description=(
            "Очистка API/прокси в accounts.json и telethon_session_dir/*.json; "
            "опционально удаление сирот. Каталог campaign (пакет рассылки) не изменяется."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ничего не записывать: только отчёт (для delete-orphans / drop-rows — как обычно)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Не спрашивать подтверждение перед деструктивными шагами",
    )
    parser.add_argument(
        "--full-account-reset",
        action="store_true",
        help=(
            "Пресет: снять API/прокси, sanitize *.json в папке сессий, очистить пул прокси и "
            "telethon_default_api, drop-missing-session-rows, delete-orphans. "
            "Не трогает campaign/ (zip, apis.txt пакета, картинки)."
        ),
    )
    parser.add_argument(
        "--no-strip-api",
        action="store_true",
        help="Не снимать api_id/api_hash",
    )
    parser.add_argument(
        "--no-strip-proxy-accounts",
        action="store_true",
        help="Не снимать proxy с записей accounts.json (sidecar всё равно чистится --sanitize-json)",
    )
    parser.add_argument(
        "--no-sanitize-json",
        action="store_true",
        help="Не проходить по всем *.json в папке сессий",
    )
    parser.add_argument(
        "--clear-proxy-pool",
        action="store_true",
        help="Очистить config/proxies.txt и proxies.list в settings.json",
    )
    parser.add_argument(
        "--clear-default-api",
        action="store_true",
        help="Обнулить telethon_default_api в settings.json",
    )
    parser.add_argument(
        "--delete-orphans",
        action="store_true",
        help="Удалить *.session и *.json, чьё имя нет в session_name в accounts.json",
    )
    parser.add_argument(
        "--drop-missing-session-rows",
        action="store_true",
        help="Удалить из accounts.json строки, для которых нет <name>.session",
    )
    args = parser.parse_args()

    if args.full_account_reset:
        args.clear_proxy_pool = True
        args.clear_default_api = True
        args.drop_missing_session_rows = True
        args.delete_orphans = True

    con = Console()
    sett = Settings()
    session_dir = telethon_session_dir_path(sett)
    campaign_root = ROOT / sett.campaign_dir

    unsafe, reason = _session_dir_collides_with_campaign(session_dir, campaign_root)
    if unsafe:
        con.print(f"[red]{escape(reason)}[/]")
        con.print(f"[dim]telethon_session_dir:[/] {escape(str(session_dir.resolve()))}")
        con.print(f"[dim]campaign_dir:[/] {escape(str(campaign_root.resolve()))}")
        return 1

    destructive = bool(
        args.delete_orphans
        or args.drop_missing_session_rows
        or args.clear_proxy_pool
        or args.clear_default_api
    )
    if destructive and not args.dry_run and not args.yes:
        con.print(
            "[red]Укажите --yes[/] для записи на диск при опциях, меняющих конфиг/удаляющих файлы, "
            "или используйте [cyan]--dry-run[/] для проверки."
        )
        return 1

    if args.dry_run:
        con.print("[yellow]Режим --dry-run:[/] пропуск записи в accounts.json, sidecar и settings.")
    if args.full_account_reset:
        con.print("[cyan]Пресет --full-account-reset[/] (campaign/ не изменяется).")

    if not args.dry_run and not args.no_strip_api:
        n_a, n_s, path = strip_api_credentials_from_accounts(sett)
        con.print(f"[green]API снят:[/] accounts.json записей={n_a}, sidecar (по именам из json)={n_s} · {path}")

    if not args.dry_run and not args.no_strip_proxy_accounts:
        n_p, n_ps, path_p = strip_proxy_from_accounts(sett)
        con.print(f"[green]Прокси снят с аккаунтов:[/] записей={n_p}, sidecar={n_ps} · {path_p}")

    if not args.dry_run and not args.no_sanitize_json:
        ch, err_n, errs = sanitize_all_session_sidecar_json_files(sett)
        con.print(f"[green]Проход по всем *.json в[/] [cyan]{session_dir}[/]: изменено={ch}, ошибок={err_n}")
        for e in errs[:20]:
            con.print(f"  [yellow]{escape(e)}[/]")
        if len(errs) > 20:
            con.print(f"  [dim]… ещё {len(errs) - 20}[/]")
    elif args.dry_run and not args.no_sanitize_json:
        con.print(f"[dim](dry-run) пропуск полного прохода *.json в {session_dir}[/]")

    if args.clear_proxy_pool:
        if args.dry_run:
            con.print("[dim](dry-run) пропуск --clear-proxy-pool[/]")
        else:
            ok, msg = clear_proxy_pool_in_config()
            con.print(f"[green]Пул прокси:[/] {msg}" if ok else f"[red]{escape(msg)}[/]")

    if args.clear_default_api:
        if args.dry_run:
            con.print("[dim](dry-run) пропуск --clear-default-api[/]")
        else:
            ok, msg = clear_telethon_default_api()
            con.print(f"[green]telethon_default_api:[/] {msg}" if ok else f"[red]{escape(msg)}[/]")

    if args.drop_missing_session_rows:
        n, names = remove_account_rows_without_session_file(sett, dry_run=args.dry_run)
        con.print(
            f"[{'yellow' if args.dry_run else 'green'}]Строк accounts.json без .session на диске:[/] {n}"
        )
        for nm in names[:30]:
            con.print(f"  [dim]{escape(nm)}[/]")
        if len(names) > 30:
            con.print(f"  [dim]… ещё {len(names) - 30}[/]")

    if args.delete_orphans:
        deleted, warns = delete_orphan_session_artifacts(sett, dry_run=args.dry_run)
        tag = "Будут удалены / удалено" if args.dry_run else "Удалено"
        con.print(f"[green]{tag} файлов (сироты в папке сессий):[/] {len(deleted)}")
        for line in deleted[:40]:
            con.print(f"  [dim]{escape(line)}[/]")
        if len(deleted) > 40:
            con.print(f"  [dim]… ещё {len(deleted) - 40}[/]")
        for w in warns:
            con.print(f"  [yellow]{escape(str(w))}[/]")

    con.print("[dim]Готово. Каталог campaign/ не изменялся.[/]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
