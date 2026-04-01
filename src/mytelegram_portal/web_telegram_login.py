"""Фаза 1: вход в Telegram Web (Playwright), без Telethon."""
from __future__ import annotations

import re
import time
from typing import Callable

from playwright.sync_api import Page

from src.config import Settings
from src.mytelegram_portal import delays as D

TELEGRAM_WEB_K = "https://web.telegram.org/k/"

LogFn = Callable[[str], None]


def _try_click_phone_login(page: Page, settings: Settings, log: LogFn) -> None:
    """С экрана QR — кнопка «Log in by phone number» (role=button), не ссылка."""
    patterns = (
        r"Log in by phone number",
        r"LOG IN BY PHONE",
        r"Log in by phone",
        r"номеру телефона",
        r"по номеру телефона",
        r"по номеру",
        r"Войти по номеру",
    )
    for pat in patterns:
        rx = re.compile(pat, re.I)
        for role in ("button", "link"):
            try:
                el = page.get_by_role(role, name=rx)
                if el.count() > 0 and el.first.is_visible(timeout=3500):
                    el.first.click(timeout=10000)
                    D.delay_after_click(settings, log)
                    return
            except Exception:
                continue


def _fill_phone_field(page: Page, phone: str, settings: Settings, log: LogFn) -> bool:
    # Telegram Web /k/: номер в contenteditable, страна — соседний .input-select
    ce_selectors = (
        'div.input-field:not(.input-select) div.input-field-input[contenteditable="true"]',
        'div.input-field-input[contenteditable="true"][inputmode="decimal"]',
    )
    for sel in ce_selectors:
        loc = page.locator(sel).first
        try:
            if loc.is_visible(timeout=6000):
                loc.click(timeout=5000)
                D.delay_after_click(settings, log)
                loc.fill(phone, timeout=15000)
                D.delay_after_type(settings, log)
                return True
        except Exception:
            continue
    try:
        tb = page.get_by_role(
            "textbox", name=re.compile(r"phone|телефон|номер", re.I)
        ).first
        if tb.is_visible(timeout=4000):
            tb.click(timeout=5000)
            tb.fill(phone, timeout=15000)
            D.delay_after_type(settings, log)
            return True
    except Exception:
        pass
    selectors = (
        'input[type="tel"]',
        'input[inputmode="numeric"]',
        "input#sign-in-phone-number",
    )
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.is_visible(timeout=4000):
                loc.click(timeout=5000)
                D.delay_after_click(settings, log)
                loc.fill("")
                loc.fill(phone, timeout=10000)
                D.delay_after_type(settings, log)
                return True
        except Exception:
            continue
    try:
        inp = page.locator("div.input-field input, .input-field input").first
        if inp.is_visible(timeout=3000):
            inp.click()
            inp.fill(phone, timeout=10000)
            D.delay_after_type(settings, log)
            return True
    except Exception:
        pass
    return False


def _click_next_or_submit(page: Page, settings: Settings, log: LogFn) -> None:
    labels = (
        r"^Next$",
        r"^NEXT$",
        r"^Далее$",
        r"^ДАЛЕЕ$",
        r"^Continue$",
        r"^Продолжить$",
    )
    for lab in labels:
        try:
            btn = page.get_by_role("button", name=re.compile(lab, re.I))
            if btn.count() > 0 and btn.first.is_visible(timeout=2000):
                btn.first.click(timeout=10000)
                D.delay_after_submit(settings, log)
                return
        except Exception:
            continue
    try:
        page.locator('button[type="submit"]').first.click(timeout=8000)
        D.delay_after_submit(settings, log)
    except Exception as e:
        raise RuntimeError(f"Не найдена кнопка «Далее» / Next: {e}") from e


def _fill_login_code(page: Page, code: str, settings: Settings, log: LogFn) -> None:
    code = (code or "").strip()
    if not code:
        raise ValueError("Пустой код входа")
    selectors = (
        'input[inputmode="numeric"]',
        'input[type="tel"]',
        'input[type="text"]',
    )
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.is_visible(timeout=6000):
                loc.click(timeout=5000)
                loc.fill(code, timeout=15000)
                D.delay_after_type(settings, log)
                _click_next_or_submit(page, settings, log)
                return
        except Exception:
            continue
    raise RuntimeError("Не найдено поле для кода входа в Telegram Web")


def _maybe_cloud_password(
    page: Page,
    settings: Settings,
    log: LogFn,
    get_password: Callable[[], str],
) -> None:
    """Облачный пароль 2FA, если появился."""
    try:
        pwd_input = page.locator('input[type="password"]').first
        if not pwd_input.is_visible(timeout=4000):
            return
    except Exception:
        return
    pwd = (get_password() or "").strip()
    if not pwd:
        raise RuntimeError("Требуется пароль 2FA")
    try:
        pwd_input.fill(pwd, timeout=10000)
        D.delay_after_type(settings, log)
        _click_next_or_submit(page, settings, log)
    except Exception as e:
        raise RuntimeError(f"Не удалось отправить пароль 2FA: {e}") from e


def _wait_main_loaded(page: Page, log: LogFn, timeout_ms: int = 180000) -> None:
    markers = (
        "div.input-message-input",
        "#column-center .input-message-input",
        ".chat-input",
        "div.composer-wrapper",
    )
    end = time.time() + timeout_ms / 1000.0
    last_err: str | None = None
    while time.time() < end:
        for sel in markers:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=2000):
                    log(f"main UI ok ({sel})")
                    return
            except Exception as e:
                last_err = str(e)
        time.sleep(2.0)
    raise RuntimeError(
        "Таймаут ожидания главного экрана Telegram Web "
        f"(последняя ошибка: {last_err})"
    )


def run_telegram_web_login(
    page: Page,
    *,
    session_name: str,
    phone: str,
    settings: Settings,
    log: LogFn,
    prompt_login_code: Callable[[str], str],
    get_2fa_password: Callable[[], str],
) -> None:
    """
    Одна страница Playwright: открыть K, войти по номеру и коду из консоли.
    """
    log(f"goto {TELEGRAM_WEB_K}")
    page.goto(TELEGRAM_WEB_K, wait_until="domcontentloaded", timeout=120000)
    D.delay_after_navigate(settings, log)
    _try_click_phone_login(page, settings, log)
    D.delay_after_navigate(settings, log)
    if not _fill_phone_field(page, phone, settings, log):
        raise RuntimeError("Поле номера телефона в Telegram Web не найдено")
    _click_next_or_submit(page, settings, log)
    raw_code = prompt_login_code(phone)
    digits = re.sub(r"\D", "", raw_code) if raw_code else ""
    code_to_use = digits if digits else (raw_code or "").strip()
    _fill_login_code(page, code_to_use, settings, log)
    _maybe_cloud_password(page, settings, log, get_2fa_password)
    try:
        _wait_main_loaded(page, log)
    except RuntimeError:
        _maybe_cloud_password(page, settings, log, get_2fa_password)
        _wait_main_loaded(page, log)
