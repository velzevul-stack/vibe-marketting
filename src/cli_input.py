"""Разбор ввода из консоли: SSH/backspace (^H) и прочий мусор в числовых полях."""
from __future__ import annotations

import re


def digits_only(s: str) -> str:
    """Оставить только цифры (остальное отбросить)."""
    return re.sub(r"\D", "", s or "")


def strip_c0_controls(s: str) -> str:
    """Убрать управляющие символы кроме таба (удобно для телефона, путей)."""
    return "".join(c for c in (s or "") if c == "\t" or ord(c) >= 32)


def parse_int_default(raw: str, default: int) -> int:
    """Целое из строки: только цифры; пусто → default; ошибка → default."""
    d = digits_only(raw)
    if not d:
        return default
    try:
        return int(d)
    except ValueError:
        return default


def parse_nonneg_int_clamped(
    raw: str,
    *,
    default: int,
    allow_zero: bool = False,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Как parse_int_default, затем ограничение диапазоном."""
    n = parse_int_default(raw, default)
    lo = 0 if allow_zero else 1
    if minimum is not None:
        lo = minimum
    if n < lo:
        n = default if default >= lo else lo
    if maximum is not None and n > maximum:
        n = maximum
    return n


def parse_api_id_digits(raw: str) -> int | None:
    """api_id: только цифры из ввода; пусто/битое → None."""
    d = digits_only(raw)
    if not d:
        return None
    try:
        return int(d)
    except ValueError:
        return None
