"""Случайные паузы между действиями Playwright (настройки mytg_delays)."""
from __future__ import annotations

import random
import time
from typing import Callable

from src.config import Settings

LogFn = Callable[[str], None] | None


def _sleep_pair(lo: float, hi: float) -> float:
    return max(0.05, random.uniform(lo, hi))


def delay_after_navigate(settings: Settings, log: LogFn = None) -> None:
    t = _sleep_pair(settings.mytg_after_navigate_min, settings.mytg_after_navigate_max)
    if log:
        log(f"delay after_navigate {t:.2f}s")
    time.sleep(t)


def delay_after_click(settings: Settings, log: LogFn = None) -> None:
    t = _sleep_pair(settings.mytg_after_click_min, settings.mytg_after_click_max)
    if log:
        log(f"delay after_click {t:.2f}s")
    time.sleep(t)


def delay_after_type(settings: Settings, log: LogFn = None) -> None:
    t = _sleep_pair(settings.mytg_after_type_min, settings.mytg_after_type_max)
    if log:
        log(f"delay after_type {t:.2f}s")
    time.sleep(t)


def delay_after_submit(settings: Settings, log: LogFn = None) -> None:
    t = _sleep_pair(settings.mytg_after_submit_min, settings.mytg_after_submit_max)
    if log:
        log(f"delay after_submit {t:.2f}s")
    time.sleep(t)


def delay_poll_portal_code(settings: Settings, log: LogFn = None) -> None:
    t = _sleep_pair(settings.mytg_poll_portal_code_min, settings.mytg_poll_portal_code_max)
    if log:
        log(f"delay poll_portal_code {t:.2f}s")
    time.sleep(t)
