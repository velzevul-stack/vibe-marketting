"""Согласованные профили браузера Playwright для mytg (разные аккаунты — разный контекст).

Индекс профиля стабилен между процессами (не встроенный hash(str)), чтобы фаза 1 и фаза 2
совпадали по UA/viewport/locale даже при отдельных запусках CLI.
"""
from __future__ import annotations

import hashlib
from typing import Any, Literal

from src.config import Settings

# Версия в UA — ориентир под недавний Chromium; при channel=chrome подставьте ближе к реальной.
_CHROME_VER = "131.0.0.0"

OsKind = Literal["win", "mac", "linux"]


def _chrome_ua(os_kind: OsKind) -> str:
    if os_kind == "win":
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{_CHROME_VER} Safari/537.36"
        )
    if os_kind == "mac":
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{_CHROME_VER} Safari/537.36"
        )
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{_CHROME_VER} Safari/537.36"
    )


def _stable_profile_index(session_name: str, n: int) -> int:
    if n <= 0:
        return 0
    digest = hashlib.sha256((session_name or "").encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % n


# (os, locale, timezone_id, width, height, accept_language, color_scheme, device_scale_factor)
_PROFILES: tuple[
    tuple[OsKind, str, str, int, int, str, str, float],
    ...,
] = (
    ("win", "en-US", "America/New_York", 1920, 1080, "en-US,en;q=0.9", "light", 1.0),
    ("win", "en-GB", "Europe/London", 1536, 864, "en-GB,en;q=0.9", "light", 1.0),
    ("mac", "en-US", "America/Los_Angeles", 1440, 900, "en-US,en;q=0.9", "dark", 1.0),
    ("mac", "de-DE", "Europe/Berlin", 1680, 1050, "de-DE,de;q=0.9,en;q=0.8", "light", 1.0),
    ("linux", "ru-RU", "Europe/Moscow", 1366, 768, "ru-RU,ru;q=0.9,en-US;q=0.5,en;q=0.4", "light", 1.0),
    ("win", "pl-PL", "Europe/Warsaw", 1600, 900, "pl-PL,pl;q=0.9,en;q=0.8", "no-preference", 1.0),
    ("mac", "fr-FR", "Europe/Paris", 1280, 800, "fr-FR,fr;q=0.9,en;q=0.8", "dark", 1.25),
    ("win", "es-ES", "Europe/Madrid", 1920, 1200, "es-ES,es;q=0.9,en;q=0.7", "light", 1.0),
    ("linux", "en-US", "Europe/Amsterdam", 1280, 720, "en-US,en;q=0.9", "light", 1.0),
    ("win", "uk-UA", "Europe/Kyiv", 1536, 960, "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7", "light", 1.0),
    ("mac", "it-IT", "Europe/Rome", 2560, 1440, "it-IT,it;q=0.9,en;q=0.8", "no-preference", 1.0),
    ("win", "pt-BR", "America/Sao_Paulo", 1360, 768, "pt-BR,pt;q=0.9,en-US;q=0.5,en;q=0.4", "dark", 1.0),
    ("linux", "de-DE", "Europe/Zurich", 1920, 1080, "de-CH,de;q=0.9,en;q=0.8", "light", 1.0),
    ("win", "tr-TR", "Europe/Istanbul", 1600, 900, "tr-TR,tr;q=0.9,en-US;q=0.5,en;q=0.4", "light", 1.25),
    ("mac", "ja-JP", "Asia/Tokyo", 1440, 900, "ja-JP,ja;q=0.9,en-US;q=0.5,en;q=0.4", "light", 1.0),
    ("win", "en-US", "America/Chicago", 1280, 800, "en-US,en;q=0.9", "dark", 1.0),
)

_LEGACY_UAS = (
    _chrome_ua("win"),
    _chrome_ua("mac"),
)


def playwright_context_options_for_mytg(session_name: str, settings: Settings) -> dict[str, Any]:
    """
    Аргументы для ``browser.new_context`` (без proxy и storage_state).

    При ``mytg_diverse_contexts`` = False — прежнее поведение: 1280×800 и два UA.
    """
    if not settings.mytg_diverse_contexts:
        i = _stable_profile_index(session_name, len(_LEGACY_UAS))
        return {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": _LEGACY_UAS[i],
        }

    idx = _stable_profile_index(session_name, len(_PROFILES))
    os_k, loc, tz, w, h, al, cs, scale = _PROFILES[idx]
    headers = {
        "Accept-Language": al,
    }
    out: dict[str, Any] = {
        "user_agent": _chrome_ua(os_k),
        "viewport": {"width": w, "height": h},
        "locale": loc,
        "timezone_id": tz,
        "color_scheme": cs,
        "extra_http_headers": headers,
    }
    if scale != 1.0:
        out["device_scale_factor"] = scale
    return out
