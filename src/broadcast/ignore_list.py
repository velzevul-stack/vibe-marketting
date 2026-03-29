"""Чёрный список username: ignor_list.txt в каталоге пакета и в config/."""
from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from src.config import _config_dir


def normalize_username_ignore_token(raw: str) -> str:
    return (raw or "").strip().lstrip("@").lower()


def _norm_header_key(s: str) -> str:
    return "".join((s or "").strip().lower().split())


def _strip_hash_comment_lines(raw: str) -> str:
    lines = []
    for line in (raw or "").splitlines():
        if line.strip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _username_column_key(fieldnames: list[str] | None) -> str | None:
    if not fieldnames:
        return None
    fnorm = {_norm_header_key(n): n for n in fieldnames if n}
    for cand in ("username", "user", "user_name"):
        if cand in fnorm:
            return fnorm[cand]
    return None


def parse_ignor_list_text(raw: str) -> frozenset[str]:
    """
    Поддержка:
    - один username на строку (с опциональным ведущим @);
    - строки CSV с заголовком, где есть колонка ``Username`` (как в экспорте участников);
    - строка ``числовой_id,username,...`` без заголовка — берётся второе поле, если первое — число.
    """
    raw = (raw or "").lstrip("\ufeff")
    body = _strip_hash_comment_lines(raw).strip()
    if not body:
        return frozenset()
    out: set[str] = set()
    first_line = ""
    for ln in body.splitlines():
        s = ln.strip()
        if s:
            first_line = s
            break
    if not first_line:
        return frozenset()

    if "," in first_line:
        sio = StringIO(body)
        reader = csv.DictReader(sio)
        ukey = _username_column_key(list(reader.fieldnames or []))
        if ukey:
            for row in reader:
                cell = (row.get(ukey) or "").strip()
                nu = normalize_username_ignore_token(cell)
                if nu:
                    out.add(nu)
            return frozenset(out)
        for row in csv.reader(StringIO(body)):
            if not row or not any((c or "").strip() for c in row):
                continue
            if (row[0] or "").strip().startswith("#"):
                continue
            if len(row) >= 2 and (row[0] or "").strip().isdigit():
                nu = normalize_username_ignore_token(row[1])
            else:
                nu = normalize_username_ignore_token(row[0])
            if nu:
                out.add(nu)
        return frozenset(out)

    for ln in body.splitlines():
        s = ln.strip()
        if not s:
            continue
        nu = normalize_username_ignore_token(s)
        if nu:
            out.add(nu)
    return frozenset(out)


def load_username_ignore_set(*, campaign_root: Path | None = None) -> frozenset[str]:
    """
    Объединяет существующие файлы (порядок: сначала каталог пакета, затем config/ignor_list.txt).
    """
    paths: list[Path] = []
    if campaign_root is not None:
        paths.append(Path(campaign_root).resolve() / "ignor_list.txt")
    paths.append(_config_dir() / "ignor_list.txt")
    merged: set[str] = set()
    for p in paths:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        merged.update(parse_ignor_list_text(text))
    return frozenset(merged)


def user_dict_username_normalized(u: dict) -> str:
    return normalize_username_ignore_token(str(u.get("username") or ""))
