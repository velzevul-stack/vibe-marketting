"""Импорт архива с парами name.json + name.session в каталог сессий Telethon."""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from src.config import Settings, load_accounts, telethon_session_dir_path
from src.session_sync import sync_sessions_dir_to_accounts


@dataclass
class ZipImportReport:
    pairs_copied: int = 0
    skipped_conflict: int = 0
    incomplete_in_zip: list[str] = field(default_factory=list)
    sync_added: int = 0
    sync_warnings: list[str] = field(default_factory=list)
    session_files_after: int = 0
    accounts_with_api: int = 0
    sessions_on_disk_without_api_row: int = 0


def _collect_pairs_from_dir(root: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    json_by_stem: dict[str, Path] = {}
    session_by_stem: dict[str, Path] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        if suf == ".json":
            json_by_stem[p.stem] = p
        elif suf == ".session":
            session_by_stem[p.stem] = p
    return json_by_stem, session_by_stem


def _count_sessions_missing_accounts(session_dir: Path) -> int:
    """*.session на диске, для которых нет полной строки в accounts.json."""
    acc_names = {
        a.get("session_name")
        for a in load_accounts()
        if a.get("session_name")
    }
    n = 0
    for p in session_dir.glob("*.session"):
        if p.stem and p.stem not in acc_names:
            n += 1
    return n


def import_sessions_zip(
    zip_path: Path,
    *,
    settings: Settings | None = None,
    on_conflict: str,  # "skip" | "overwrite"
) -> ZipImportReport:
    """
    Распаковать ZIP, найти пары stem.json + stem.session, скопировать в telethon_session_dir.
    Затем sync_sessions_dir_to_accounts.
    """
    s = settings or Settings()
    report = ZipImportReport()
    dest_dir = telethon_session_dir_path(s)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not zip_path.is_file():
        raise FileNotFoundError(str(zip_path))

    with tempfile.TemporaryDirectory(prefix="vibe_zip_import_") as tmp:
        tmp_root = Path(tmp)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_root)

        jmap, smap = _collect_pairs_from_dir(tmp_root)
        stems_json = set(jmap.keys())
        stems_sess = set(smap.keys())
        paired = sorted(stems_json & stems_sess)
        only_j = stems_json - stems_sess
        only_s = stems_sess - stems_json
        report.incomplete_in_zip = sorted(
            [f"{x}: только .json" for x in only_j] + [f"{x}: только .session" for x in only_s]
        )

        for stem in paired:
            src_j = jmap[stem]
            src_s = smap[stem]
            dst_j = dest_dir / f"{stem}.json"
            dst_s = dest_dir / f"{stem}.session"
            if dst_s.exists() or dst_j.exists():
                if on_conflict == "skip":
                    report.skipped_conflict += 1
                    continue
            shutil.copy2(src_s, dst_s)
            shutil.copy2(src_j, dst_j)
            report.pairs_copied += 1

    report.sync_added, report.sync_warnings = sync_sessions_dir_to_accounts(s)
    report.session_files_after = len(list(dest_dir.glob("*.session")))
    report.accounts_with_api = len(load_accounts())
    report.sessions_on_disk_without_api_row = _count_sessions_missing_accounts(dest_dir)
    return report


def print_zip_import_report(console: Console, r: ZipImportReport) -> None:
    console.print(f"[green]Скопировано пар[/] (.json + .session): [bold]{r.pairs_copied}[/]")
    if r.skipped_conflict:
        console.print(f"[yellow]Пропущено из-за конфликта имён на диске:[/] {r.skipped_conflict}")
    if r.incomplete_in_zip:
        console.print("[dim]Неполные пары в архиве (не импортированы как пара):[/]")
        for line in r.incomplete_in_zip[:25]:
            console.print(f"  [dim]{escape(line)}[/]")
        if len(r.incomplete_in_zip) > 25:
            console.print(f"  [dim]… ещё {len(r.incomplete_in_zip) - 25}[/]")
    console.print(
        f"[dim]sync_sessions → accounts.json:[/] [cyan]+{r.sync_added}[/] новых/обновлённых записей"
    )
    for w in r.sync_warnings[:15]:
        console.print(f"  [yellow]{escape(str(w))}[/]")
    if len(r.sync_warnings) > 15:
        console.print(f"  [dim]… предупреждений: {len(r.sync_warnings) - 15}[/]")
    console.print(
        f"[dim].session в папке:[/] {r.session_files_after} · "
        f"[dim]аккаунтов с api в JSON:[/] {r.accounts_with_api}"
    )
    if r.sessions_on_disk_without_api_row:
        console.print(
            f"[yellow]На диске есть .session без строки в accounts.json (нужен api в sidecar или "
            f"telethon_default_api в settings):[/] [bold]{r.sessions_on_disk_without_api_row}[/]"
        )
