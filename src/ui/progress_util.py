"""Общие индикаторы загрузки для Rich-консоли."""
from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console


@contextmanager
def console_loading(console: "Console", message: str):
    """Короткий спиннер на время синхронного или async-блока (внутри with await …)."""
    with console.status(message, spinner="dots"):
        yield
