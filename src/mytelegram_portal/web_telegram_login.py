"""Фаза 1: вход в Telegram Web (Playwright), без Telethon."""
from __future__ import annotations

import re
import time
from typing import Callable

from playwright.sync_api import Locator, Page

from src.config import Settings
from src.mytelegram_portal import delays as D

TELEGRAM_WEB_K = "https://web.telegram.org/k/"

LogFn = Callable[[str], None]

_SIGN_TAB = ".tabs-tab.page-sign"
_AUTH_TAB = ".tabs-tab.page-authCode"


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


def _ensure_phone_form_visible(page: Page, settings: Settings, log: LogFn) -> None:
    """QR по умолчанию: несколько попыток открыть форму с номером."""
    for _ in range(6):
        if _is_phone_field_visible(page, quick_ms=900):
            return
        _try_click_phone_login(page, settings, log)
        D.delay_after_navigate(settings, log)


def _is_phone_field_visible(page: Page, *, quick_ms: int) -> bool:
    locs = (
        page.locator(f"{_SIGN_TAB} div.input-field-phone div.input-field-input[contenteditable='true']"),
        page.locator("div.input-field-phone div.input-field-input[contenteditable='true']"),
        page.locator(f"{_SIGN_TAB} div.input-field:not(.input-select) div.input-field-input[contenteditable='true']"),
    )
    for loc in locs:
        try:
            if loc.first.is_visible(timeout=quick_ms):
                return True
        except Exception:
            continue
    return False


def _fill_phone_field(page: Page, phone: str, settings: Settings, log: LogFn) -> bool:
    # Актуальная разметка K: div.input-field.input-field-phone + contenteditable
    sign = page.locator(_SIGN_TAB)
    ce_selectors = (
        "div.input-field-phone div.input-field-input[contenteditable='true']",
        "div.input-field.input-field-phone div.input-field-input[contenteditable='true']",
        "div.input-field:not(.input-select) div.input-field-input[contenteditable='true']",
        'div.input-field-input[contenteditable="true"][inputmode="decimal"]',
    )
    for root in (sign, page):
        for sel in ce_selectors:
            loc = root.locator(sel).first
            try:
                if loc.is_visible(timeout=7000):
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
        if tb.is_visible(timeout=5000):
            tb.click(timeout=5000)
            tb.fill(phone, timeout=15000)
            D.delay_after_type(settings, log)
            return True
    except Exception:
        pass
    selectors = (
        f"{_SIGN_TAB} input[type='tel']",
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


def _auth_root(page: Page) -> Locator:
    return page.locator(_AUTH_TAB)


def _fill_multi_otp_boxes(boxes: Locator, code: str, settings: Settings, log: LogFn) -> bool:
    n = boxes.count()
    if n < len(code) or n < 4:
        return False
    for i, ch in enumerate(code):
        try:
            cell = boxes.nth(i)
            if not cell.is_visible(timeout=1500):
                return False
            cell.click(timeout=3000)
            cell.fill(ch, timeout=5000)
        except Exception:
            return False
    D.delay_after_type(settings, log)
    return True


def _fill_login_code(page: Page, code: str, settings: Settings, log: LogFn) -> None:
    code = (code or "").strip()
    if not code:
        raise ValueError("Пустой код входа")

    auth = _auth_root(page)
    # После Next DOM переключается на page-authCode; поля часто появляются с задержкой
    D.delay_after_navigate(settings, log)

    deadline = time.time() + 28.0
    last_err = ""

    while time.time() < deadline:
        try:
            # 1) Несколько одноциферных полей в .input-wrapper
            multi = auth.locator(
                ".input-wrapper input:not([type='hidden']):not([type='checkbox']):not([type='radio'])"
            )
            mc = multi.count()
            if mc >= max(4, len(code)):
                if _fill_multi_otp_boxes(multi, code, settings, log):
                    _click_next_or_submit(page, settings, log)
                    return

            # 2) contenteditable (как номер телефона, но на вкладке кода)
            ce = auth.locator('div.input-field-input[contenteditable="true"]').first
            if ce.is_visible(timeout=700):
                ce.click(timeout=4000)
                ce.fill(code, timeout=15000)
                D.delay_after_type(settings, log)
                _click_next_or_submit(page, settings, log)
                return

            # 3) Один input (только внутри auth — не цепляем поле номера на page-sign)
            for sel in (
                'input[autocomplete="one-time-code"]',
                'input[inputmode="numeric"]',
                'input[type="tel"]',
                'input[type="text"]',
            ):
                loc = auth.locator(sel).first
                try:
                    if loc.is_visible(timeout=900):
                        loc.click(timeout=4000)
                        loc.fill(code, timeout=15000)
                        D.delay_after_type(settings, log)
                        _click_next_or_submit(page, settings, log)
                        return
                except Exception as e:
                    last_err = str(e)
                    continue

            # 4) textbox по доступному имени (Code / Код …)
            try:
                tb = auth.get_by_role(
                    "textbox",
                    name=re.compile(
                        r"code|код|sms|telegram|digit|цифр|подтвержд", re.I
                    ),
                ).first
                if tb.is_visible(timeout=800):
                    tb.click(timeout=4000)
                    tb.fill(code, timeout=15000)
                    D.delay_after_type(settings, log)
                    _click_next_or_submit(page, settings, log)
                    return
            except Exception as e:
                last_err = str(e)

            # 5) Любой видимый input внутри auth (кроме hidden)
            any_inp = auth.locator("input").filter(has_not=page.locator("[type='hidden']"))
            ac = any_inp.count()
            for i in range(min(ac, 12)):
                loc = any_inp.nth(i)
                try:
                    if loc.is_visible(timeout=400):
                        t = (loc.get_attribute("type") or "").lower()
                        if t in ("checkbox", "radio", "submit", "button"):
                            continue
                        loc.click(timeout=3000)
                        loc.fill(code, timeout=15000)
                        D.delay_after_type(settings, log)
                        _click_next_or_submit(page, settings, log)
                        return
                except Exception as e:
                    last_err = str(e)
        except Exception as e:
            last_err = str(e)

        time.sleep(0.45)

    raise RuntimeError(
        "Не найдено поле для кода входа в Telegram Web "
        f"(вкладка {_AUTH_TAB}; последняя ошибка: {last_err or '—'})"
    )


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
    _ensure_phone_form_visible(page, settings, log)
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
