"""Экспорт и импорт списка групп в текстовом формате (как group_links.txt: одна t.me-ссылка на строку)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from src.config import load_groups_from_links_txt


def _group_link_key(g: dict) -> str:
    return str(g.get("link") or g.get("id") or "").strip().lower()


def _merge_unique_group_lists(*lists: list[list[dict]]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for lst in lists:
        for g in lst:
            k = _group_link_key(g)
            if not k or "t.me" not in k:
                continue
            if k in seen:
                continue
            seen.add(k)
            out.append(g)
    return out


def _link_from_group_dict(g: dict) -> str | None:
    """Нормализованная https://t.me/... ссылка для строки txt."""
    link = (g.get("link") or "").strip()
    if link:
        low = link.lower()
        if "t.me/" in low or "telegram.me/" in low:
            if not link.startswith("http"):
                link = "https://" + link.lstrip("/")
            return link
    raw_id = g.get("id")
    if raw_id is None:
        return None
    tail = str(raw_id).strip().strip("/")
    if not tail or "t.me" in tail.lower():
        return None
    return f"https://t.me/{tail}"


def load_found_groups_list(found_path: Path) -> tuple[list[dict] | None, str | None]:
    """Прочитать output/found_groups.json как список dict. Ошибка → (None, сообщение)."""
    if not found_path.is_file():
        return [], None
    try:
        raw = found_path.read_text(encoding="utf-8").strip()
        data = json.loads(raw) if raw else []
    except (OSError, json.JSONDecodeError) as e:
        return None, str(e)
    if not isinstance(data, list):
        return None, "ожидался JSON-массив"
    return [x for x in data if isinstance(x, dict)], None


def export_groups_to_txt(groups: list[dict], out_path: Path) -> tuple[bool, str, int]:
    """
    Записать txt: заголовок-комментарии + по одной ссылке на строку.
    Возвращает (успех, путь или сообщение об ошибке, число записанных ссылок).
    """
    header = [
        "# Vibe Marketing — экспорт групп (формат как config/group_links.txt)",
        "# Импорт: меню → 2 → 3. Строки с # в начале при разборе пропускаются.",
        "",
    ]
    lines_out: list[str] = list(header)
    seen: set[str] = set()
    n = 0
    for g in groups:
        link = _link_from_group_dict(g)
        if not link:
            continue
        k = _group_link_key({"link": link})
        if not k or k in seen:
            continue
        seen.add(k)
        title = (g.get("title") or "").strip()
        if title:
            safe = title.replace("\n", " ").replace("\r", "")[:120]
            lines_out.append(f"# {safe}")
        lines_out.append(link)
        n += 1
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    except OSError as e:
        return False, str(e), 0
    return True, str(out_path.resolve()), n


def import_txt_to_found_groups(
    txt_path: Path,
    found_path: Path,
    mode: Literal["replace", "append"],
) -> tuple[bool, str, int]:
    """
    Прочитать txt (load_groups_from_links_txt), объединить с found_groups.json и записать.
    Возвращает (успех, сообщение, итоговое число групп в JSON).
    """
    new_groups = load_groups_from_links_txt(path=txt_path)
    if not new_groups:
        return False, "В файле нет строк с ссылками t.me / telegram.me", 0

    existing, err = load_found_groups_list(found_path)
    if err:
        return False, f"Не удалось прочитать found_groups.json: {err}", 0
    assert existing is not None

    if mode == "replace":
        merged = _merge_unique_group_lists(new_groups)
    else:
        merged = _merge_unique_group_lists(existing, new_groups)

    try:
        found_path.parent.mkdir(parents=True, exist_ok=True)
        found_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        return False, str(e), 0
    return True, str(found_path.resolve()), len(merged)
