"""Извлечь код подтверждения my.telegram.org из текста сообщения Telegram."""
from __future__ import annotations

import re

# RU: «Вот он:» / EN variants; код буквенно-цифровой как в письме пользователя
_AFTER_COLON = re.compile(
    r"(?:Вот он\s*:|Vot on\s*:|here\s*(?:is|it)\s*:\s*|code\s*:\s*)\s*([A-Za-z0-9_-]{8,64})",
    re.IGNORECASE | re.MULTILINE,
)
# Строка с отдельным токеном после переноса (типичный формат сервиса)
_LINE_TOKEN = re.compile(
    r"(?:^|\n)\s*([A-Za-z0-9_-]{8,32})\s*(?:\n|$)",
    re.MULTILINE,
)
_MYTELEGRAM_HINT = re.compile(
    r"my\.telegram\.org",
    re.IGNORECASE,
)


def extract_portal_confirmation_code(text: str) -> str | None:
    """
    Возвращает первый похожий код из текста сервисного сообщения.
    Не гарантирует отсутствие ложных срабатываний на произвольный текст — вызывать на узком DOM.
    """
    if not text or not _MYTELEGRAM_HINT.search(text):
        return None
    m = _AFTER_COLON.search(text)
    if m:
        return m.group(1).strip()
    # fallback: последняя «короткая» отдельная строка из букв/цифр
    candidates = _LINE_TOKEN.findall(text)
    for c in reversed(candidates):
        if len(c) >= 8 and all(ch.isalnum() or ch in "_-" for ch in c):
            return c.strip()
    return None
