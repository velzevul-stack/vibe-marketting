"""Полное ожидание FloodWait от Telegram с выводом длительности (секунды из RPC или разбор текста)."""
from __future__ import annotations

import asyncio
import math
import re
import time

from rich.console import Console
from rich.markup import escape
from telethon.errors import FloodWaitError

_FLOOD_WAIT_RE = re.compile(r"FLOOD_WAIT_(\d+)", re.I)


def flood_wait_seconds(exc: BaseException) -> int:
    """
    Сколько секунд ждать по ошибке. У FloodWaitError — поле seconds;
    иначе пробуем вытащить FLOOD_WAIT_N из текста сообщения.
    """
    if isinstance(exc, FloodWaitError):
        s = getattr(exc, "seconds", None)
        if s is not None:
            n = int(s)
            if n > 0:
                return n
    msg = str(exc)
    m = _FLOOD_WAIT_RE.search(msg)
    if m:
        return int(m.group(1))
    s = getattr(exc, "seconds", None)
    if s is not None:
        return max(0, int(s))
    return 0


async def sleep_flood_wait(
    seconds: int,
    *,
    console: Console | None = None,
    session_label: str = "",
    prefix: str = "FloodWait",
) -> None:
    """
    Ждём полный интервал. С Rich — полоса и «осталось N с»; без консоли — print + sleep.
    """
    wait = max(0, int(seconds))
    if wait <= 0:
        return
    label = session_label or "?"

    if console is not None:
        sess = escape(str(label))
        console.print(
            f"\n[yellow]{escape(prefix)}:[/] полное ожидание [bold]{wait}[/] с "
            f"([dim]сессия[/] [cyan]{sess}[/])"
        )
        console.print("[dim]Таймер (обновление ~1 с)…[/]")
        total = float(wait)
        deadline = time.monotonic() + total
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            left_i = max(0, math.ceil(left - 1e-9))
            elapsed = total - left
            pct = min(100.0, max(0.0, 100.0 * elapsed / total)) if total else 100.0
            bar_w = 20
            filled = min(bar_w, max(0, int(bar_w * pct / 100.0 + 0.5)))
            bar = "█" * filled + "░" * (bar_w - filled)
            console.print(
                f"\r[green][{bar}][/] [yellow]осталось[/] [bold]{left_i}[/] с [dim]({pct:.1f}%)[/]  ",
                end="",
            )
            await asyncio.sleep(min(1.0, max(left, 0)))
        console.print(f"\n[dim]{escape(prefix)}: ожидание завершено, продолжаем.[/]")
    else:
        print(f"{prefix}: полное ожидание {wait} с (сессия {label})")
        await asyncio.sleep(wait)
