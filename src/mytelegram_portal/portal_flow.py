"""Фаза 2: my.telegram.org — код из Telegram Web, создание/чтение приложения."""
from __future__ import annotations

import re
import secrets
import string
import time
from typing import Callable

from playwright.sync_api import BrowserContext

from src.config import Settings
from src.mytelegram_portal import delays as D
from src.mytelegram_portal.code_parse import extract_portal_confirmation_code
from src.mytelegram_portal.state import AccountJob
from src.mytelegram_portal.web_telegram_login import TELEGRAM_WEB_K

LogFn = Callable[[str], None]

MYTELEGRAM_HOME = "https://my.telegram.org/"

# Реальные URL для поля «сайт» (необязательно уникальны на каждый прогон — суффикс в title)
_FALLBACK_APP_URLS = (
    "https://en.wikipedia.org/wiki/Albedo",
    "https://en.wikipedia.org/wiki/Rivet",
    "https://en.wikipedia.org/wiki/Sextant",
    "https://en.wikipedia.org/wiki/Barograph",
)

_PLATFORM_VALUES = ("android", "desktop", "web", "ios", "other", "wp", "bb", "ubp")


def _random_app_names() -> tuple[str, str]:
    suf = secrets.token_hex(3)
    title = f"Notes App {suf[:5]}"
    alphabet = string.ascii_lowercase + string.digits
    short = "".join(secrets.choice(alphabet) for _ in range(12))
    short = f"app{suf[:6]}{short}"[:32]
    if len(short) < 5:
        short = (short + "x" * 5)[:8]
    return title, short


def _random_app_url() -> str:
    return secrets.choice(_FALLBACK_APP_URLS)


def _click_to_reveal_spoilers(tg_page, log: LogFn) -> None:
    """Click on spoiler-wrapped elements so hidden codes become readable."""
    # Path 1: Firefox / older builds — <span class="spoiler">
    try:
        spoilers = tg_page.locator("span.spoiler")
        cnt = spoilers.count()
        if cnt > 0:
            log(f"found {cnt} .spoiler span(s), clicking to reveal")
            for i in range(cnt):
                try:
                    spoilers.nth(i).click(timeout=2000, force=True)
                except Exception:
                    pass
            time.sleep(0.6)
            return
    except Exception:
        pass

    # Path 2: Chromium — DotRenderer canvas overlay with braille text.
    # Dispatch click events on recent message bubbles that contain braille.
    try:
        revealed = tg_page.evaluate("""() => {
            const brailleRe = /[\\u2800-\\u28ff]{3,}/;
            let count = 0;
            document.querySelectorAll(
                '.bubble-content .message, .bubble-content .message-text'
            ).forEach(el => {
                if (brailleRe.test(el.textContent || '')) {
                    el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    count++;
                }
            });
            if (!count) {
                document.querySelectorAll('.bubble .inner').forEach(el => {
                    if (brailleRe.test(el.textContent || '')) {
                        el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        count++;
                    }
                });
            }
            return count;
        }""")
        if revealed:
            log(f"dispatched click on {revealed} bubble(s) with braille spoilers")
            time.sleep(0.6)
    except Exception:
        pass


def _scrape_portal_code_from_telegram_page(tg_page, settings: Settings, log: LogFn) -> str | None:
    _click_to_reveal_spoilers(tg_page, log)
    try:
        blob = tg_page.inner_text("body", timeout=15000)
    except Exception:
        return None
    return extract_portal_confirmation_code(blob)


def _open_telegram_service_chat(tg_page, settings: Settings, log: LogFn) -> None:
    try:
        rows = tg_page.locator(".chatlist-chat, .ListItem-chat, a.row").filter(
            has_text=re.compile(r"Telegram", re.I)
        )
        if rows.count() > 0 and rows.first.is_visible(timeout=6000):
            rows.first.click(timeout=12000)
            D.delay_after_click(settings, log)
            log("opened Telegram row in sidebar")
            return
    except Exception as e:
        log(f"sidebar Telegram click: {e}")
    try:
        tg_page.keyboard.press("Control+KeyK")
        D.delay_after_click(settings, log)
        tg_page.keyboard.type("Telegram", delay=40)
        D.delay_after_type(settings, log)
        time.sleep(1.2)
        tg_page.keyboard.press("Enter")
        D.delay_after_submit(settings, log)
        log("opened Telegram via Ctrl+K search")
    except Exception as e:
        log(f"Ctrl+K search failed: {e}")


def _poll_portal_code(
    tg_page,
    settings: Settings,
    log: LogFn,
) -> str | None:
    deadline = time.time() + settings.mytg_portal_code_timeout_sec
    while time.time() < deadline:
        code = _scrape_portal_code_from_telegram_page(tg_page, settings, log)
        if code:
            log(f"portal code detected ({len(code)} chars)")
            return code
        D.delay_poll_portal_code(settings, log)
    return None


def _fill_my_phone(portal_page, phone: str, settings: Settings, log: LogFn) -> None:
    try:
        loc = portal_page.get_by_label(
            re.compile(
                r"Your Phone Number|phone number|номер телефона|телефон",
                re.I,
            )
        ).first
        if loc.is_visible(timeout=4000):
            loc.fill(phone, timeout=15000)
            D.delay_after_type(settings, log)
            log("phone filled (label)")
            return
    except Exception:
        pass
    try:
        loc = portal_page.get_by_placeholder(
            re.compile(r"\+?\d", re.I)
        ).first
        if loc.is_visible(timeout=3000):
            loc.fill(phone, timeout=15000)
            D.delay_after_type(settings, log)
            log("phone filled (placeholder)")
            return
    except Exception:
        pass
    for sel in (
        "#my_send_form #my_login_phone",
        "#my_login_phone",
        'input#my_login_phone',
        'form#my_send_form input[type="text"]',
        'input[name="phone"]',
        'input[type="tel"]',
        "input#phone-number",
    ):
        loc = portal_page.locator(sel).first
        try:
            if loc.is_visible(timeout=5000):
                loc.fill(phone, timeout=15000)
                D.delay_after_type(settings, log)
                log(f"phone filled ({sel})")
                return
        except Exception:
            continue
    raise RuntimeError("Поле телефона на my.telegram.org не найдено")


def _wait_portal_phone_accepted(
    portal_page, log: LogFn, timeout_sec: float = 30,
) -> None:
    """After clicking submit, wait for AJAX: code form visible or error alert."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            alert = portal_page.locator("#my_login_alert .alert-danger")
            if alert.count() > 0 and alert.first.is_visible(timeout=300):
                err_text = alert.first.inner_text(timeout=3000).strip()
                if err_text:
                    raise RuntimeError(f"my.telegram.org отклонил номер: {err_text}")
        except RuntimeError:
            raise
        except Exception:
            pass
        try:
            if portal_page.locator("#my_password").is_visible(timeout=500):
                log("phone accepted — code form visible")
                return
        except Exception:
            pass
        time.sleep(0.5)
    log("warning: timeout waiting for code form after phone submit")


def _portal_submit_phone(portal_page, settings: Settings, log: LogFn) -> None:
    for pat in (r"Next", r"Далее", r"Submit", r"Send", r"Отправить"):
        try:
            btn = portal_page.get_by_role("button", name=re.compile(pat, re.I))
            if btn.count() > 0 and btn.first.is_visible(timeout=3000):
                log(f"submit phone: clicking '{pat}'")
                btn.first.click(timeout=15000)
                D.delay_after_submit(settings, log)
                _wait_portal_phone_accepted(portal_page, log)
                return
        except Exception:
            continue
    log("submit phone: role buttons not found, trying CSS selectors")
    try:
        portal_page.locator("form#my_send_form button[type='submit']").first.click(
            timeout=15000
        )
        D.delay_after_submit(settings, log)
        _wait_portal_phone_accepted(portal_page, log)
        return
    except Exception:
        pass
    try:
        portal_page.locator("#my_send_form .btn-primary").first.click(timeout=15000)
        D.delay_after_submit(settings, log)
        _wait_portal_phone_accepted(portal_page, log)
    except Exception as e:
        raise RuntimeError(f"Не удалось отправить телефон на my.telegram.org: {e}") from e


def _fill_portal_confirmation_code(
    portal_page, code: str, settings: Settings, log: LogFn
) -> None:
    code = (code or "").strip()
    if not code:
        raise ValueError("Пустой код подтверждения my.telegram.org")
    try:
        loc = portal_page.get_by_label(
            re.compile(r"Confirmation code|код подтверждения|confirmation", re.I)
        ).first
        if loc.is_visible(timeout=5000):
            loc.fill(code, timeout=15000)
            D.delay_after_type(settings, log)
            return
    except Exception:
        pass
    for sel in (
        "#my_login_form #my_password",
        "#my_password",
        'input#my_password[name="password"]',
        'form#my_login_form input[name="password"]',
        'input[name="password"]',
        'input[name="random_hash"]',
        'input[type="text"]',
        'input[type="tel"]',
    ):
        loc = portal_page.locator(sel).first
        try:
            if loc.is_visible(timeout=6000):
                loc.fill(code, timeout=15000)
                D.delay_after_type(settings, log)
                return
        except Exception:
            continue
    raise RuntimeError("Поле кода подтверждения на my.telegram.org не найдено")


def _portal_sign_in(portal_page, settings: Settings, log: LogFn) -> None:
    for pat in (r"Sign\s*In", r"Войти", r"Submit", r"Вход"):
        try:
            btn = portal_page.get_by_role("button", name=re.compile(pat, re.I))
            if btn.count() > 0 and btn.first.is_visible(timeout=3000):
                log(f"sign in: clicking '{pat}'")
                btn.first.click(timeout=20000)
                D.delay_after_submit(settings, log)
                return
        except Exception:
            continue
    log("sign in: role buttons not found, trying CSS selector")
    try:
        portal_page.locator("form#my_login_form button[type='submit']").first.click(
            timeout=20000
        )
        D.delay_after_submit(settings, log)
    except Exception as e:
        raise RuntimeError(f"Кнопка Sign In на my.telegram.org не найдена: {e}") from e


def _goto_api_tools(portal_page, settings: Settings, log: LogFn) -> None:
    D.delay_after_navigate(settings, log)
    try:
        link = portal_page.get_by_role("link", name=re.compile(r"API development tools", re.I))
        if link.count() > 0:
            link.first.click(timeout=20000)
            D.delay_after_click(settings, log)
            return
    except Exception:
        pass
    try:
        portal_page.goto(MYTELEGRAM_HOME + "apps", timeout=60000)
        D.delay_after_navigate(settings, log)
    except Exception as e:
        raise RuntimeError(f"Не удалось открыть API development tools: {e}") from e


def _parse_api_from_page_text(portal_page) -> tuple[int | None, str | None]:
    try:
        text = portal_page.inner_text("body", timeout=20000)
    except Exception:
        return None, None
    m_id = re.search(
        r"(?:app\s*api[_\s]?id|api[_\s]?id|App api_id)\s*[:\s]\s*(\d{4,12})",
        text,
        re.I,
    )
    m_hash = re.search(
        r"(?:app\s*api[_\s]?hash|api[_\s]?hash)\s*[:\s]\s*([a-fA-F0-9]{32})",
        text,
        re.I,
    )
    aid = int(m_id.group(1)) if m_id else None
    ahash = m_hash.group(1).lower() if m_hash else None
    return aid, ahash


def _try_create_application(
    portal_page,
    settings: Settings,
    log: LogFn,
    platform_value: str,
) -> tuple[bool, str]:
    title, short = _random_app_names()
    url = _random_app_url()
    try:
        fills = (
            ("App title", "app_title", title),
            ("Short name", "app_shortname", short),
            ("URL", "app_url", url),
        )
        for label, name_attr, val in fills:
            filled = False
            for sel in (
                f"#{name_attr}",
                f'input[name="{name_attr}"]',
                f'textarea[name="{name_attr}"]',
            ):
                try:
                    loc = portal_page.locator(sel).first
                    if loc.is_visible(timeout=2000):
                        loc.fill(val, timeout=12000)
                        D.delay_after_type(settings, log)
                        filled = True
                        break
                except Exception:
                    continue
            if not filled:
                try:
                    portal_page.get_by_label(
                        re.compile(label, re.I)
                    ).first.fill(val, timeout=12000)
                    D.delay_after_type(settings, log)
                except Exception:
                    portal_page.locator(
                        f'input[name="{name_attr}"], textarea[name="{name_attr}"]'
                    ).first.fill(val, timeout=12000)
                    D.delay_after_type(settings, log)
        try:
            portal_page.locator(f'input[type="radio"][value="{platform_value}"]').first.check(
                timeout=8000
            )
            D.delay_after_click(settings, log)
        except Exception:
            portal_page.get_by_label(re.compile("Android|Platform", re.I)).first.click(
                timeout=5000
            )
        create = portal_page.get_by_role(
            "button", name=re.compile(r"Create\s+application|Create", re.I)
        )
        if create.count() == 0:
            create = portal_page.locator("#app_save_btn")
        if create.count() == 0:
            create = portal_page.locator('button[type="submit"]')
        create.first.click(timeout=20000)
        D.delay_after_submit(settings, log)
        time.sleep(3.0)
        body = portal_page.inner_text("body", timeout=15000).lower()
        if "error" in body and ("sorry" in body or "try again" in body or "limit" in body):
            return False, "create_error_page"
        aid, ahash = _parse_api_from_page_text(portal_page)
        if aid and ahash:
            return True, "ok"
        return False, "no_keys_after_create"
    except Exception as e:
        return False, str(e)


def run_mytelegram_portal(
    context: BrowserContext,
    job: AccountJob,
    settings: Settings,
    log: LogFn,
    prompt_portal_code: Callable[[], str],
) -> tuple[int, str]:
    """
    Возвращает (api_id, api_hash). Поднимает две вкладки: my.telegram.org и Telegram Web.
    """
    phone = job.phone.strip()
    if not phone:
        raise ValueError("Пустой phone в job")

    portal = context.new_page()
    tg = context.new_page()

    log("portal: open my.telegram.org")
    portal.goto(MYTELEGRAM_HOME, wait_until="domcontentloaded", timeout=120000)
    D.delay_after_navigate(settings, log)
    _fill_my_phone(portal, phone, settings, log)
    _portal_submit_phone(portal, settings, log)

    log("tg: open Telegram Web for code")
    tg.goto(TELEGRAM_WEB_K, wait_until="domcontentloaded", timeout=120000)
    D.delay_after_navigate(settings, log)
    log("waiting for Telegram Web render")
    time.sleep(2.0)
    _open_telegram_service_chat(tg, settings, log)
    log("waiting for chat to load")
    time.sleep(1.5)
    _click_to_reveal_spoilers(tg, log)

    log("polling for portal confirmation code")
    code = _poll_portal_code(tg, settings, log)
    if not code:
        log("auto code failed — manual input")
        code = (prompt_portal_code() or "").strip()
    if not code:
        raise RuntimeError("Код my.telegram.org не получен")

    log("switching to portal tab for code entry")
    portal.bring_to_front()
    _fill_portal_confirmation_code(portal, code, settings, log)
    _portal_sign_in(portal, settings, log)
    log("waiting after sign in")
    time.sleep(2.0)

    _goto_api_tools(portal, settings, log)
    log("waiting for API tools page")
    time.sleep(2.0)

    api_id, api_hash = _parse_api_from_page_text(portal)
    if api_id and api_hash:
        return api_id, api_hash

    log("create new application")
    err_last = ""
    n_plat = len(_PLATFORM_VALUES)
    for i, plat in enumerate(_PLATFORM_VALUES):
        ok, err = _try_create_application(portal, settings, log, plat)
        api_id, api_hash = _parse_api_from_page_text(portal)
        if api_id and api_hash:
            return api_id, api_hash
        err_last = err or "no_keys"
        log(f"create platform={plat} → {err_last}")
        if i + 1 >= n_plat:
            break
        wait_sec = settings.mytg_create_retry_wait_sec
        log(f"wait {wait_sec:.0f}s then next platform")
        w0 = time.time()
        while time.time() - w0 < wait_sec:
            left = wait_sec - (time.time() - w0)
            if int(left) % 120 < 1 and left > 1:
                log(f"… осталось ~{int(left)}s")
            time.sleep(min(15.0, max(0.5, left)))

    raise RuntimeError(
        f"Не удалось получить api_id/api_hash (последнее: {err_last})"
    )
