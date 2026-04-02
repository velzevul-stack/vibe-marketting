"""Запуск Chromium и преобразование proxy URL для Playwright."""
from __future__ import annotations

import os
import sys
from typing import Any

from urllib.parse import urlparse, unquote

from playwright.sync_api import Browser, Playwright
from rich.console import Console

from src.config import Settings


def playwright_proxy_from_url(proxy_url: str) -> dict[str, str] | None:
    """
    HTTP(S) или SOCKS URL → dict для ``browser.new_context(proxy=...)``.
    Пустой или невалидный URL → None (без прокси).
    """
    raw = (proxy_url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    p = urlparse(raw)
    if not p.scheme or not p.hostname:
        return None
    scheme = p.scheme.lower()
    if scheme not in ("http", "https", "socks5", "socks4"):
        return None
    port = p.port or (443 if scheme == "https" else 1080 if scheme.startswith("socks") else 80)
    server = f"{scheme}://{p.hostname}:{port}"
    out: dict[str, str] = {"server": server}
    if p.username:
        out["username"] = unquote(p.username)
    if p.password:
        out["password"] = unquote(p.password)
    return out


def chromium_launch_args() -> list[str]:
    args = ["--disable-blink-features=AutomationControlled"]
    if sys.platform.startswith("linux") or os.environ.get("MYTG_NO_SANDBOX", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        args.append("--no-sandbox")
    return args


def _linux_has_gui_display() -> bool:
    return bool(
        (os.environ.get("DISPLAY") or "").strip()
        or (os.environ.get("WAYLAND_DISPLAY") or "").strip()
    )


def launch_browser(
    p: Playwright,
    settings: Settings,
    *,
    console: Console | None = None,
) -> Browser:
    """
    На Linux без DISPLAY/WAYLAND ``mytg_headless: false`` даёт «Missing X server» —
    принудительно включаем headless и пишем подсказку (xvfb-run / true в settings).
    """
    headless = settings.mytg_headless
    if os.environ.get("MYTG_FORCE_HEADLESS", "").lower() in ("1", "true", "yes"):
        headless = True
    elif (
        not headless
        and sys.platform.startswith("linux")
        and not _linux_has_gui_display()
    ):
        headless = True
        msg = (
            "[yellow][mytg][/] Нет [cyan]DISPLAY[/] / [cyan]WAYLAND_DISPLAY[/] — Chromium в "
            "[bold]headless[/] (иначе «Missing X server»). "
            "Для не-headless: [cyan]xvfb-run -a python …[/] или [cyan]mytg_headless: false[/] на машине с GUI."
        )
        if console is not None:
            console.print(msg)
        else:
            print(
                "[mytg] Нет DISPLAY/WAYLAND_DISPLAY — Chromium в headless. "
                "Для окон: xvfb-run -a python …; на ПК с GUI: mytg_headless: false.",
                file=sys.stderr,
            )

    kwargs: dict[str, Any] = {
        "headless": headless,
        "args": chromium_launch_args(),
    }
    if settings.mytg_chromium_channel:
        kwargs["channel"] = settings.mytg_chromium_channel
    return p.chromium.launch(**kwargs)
