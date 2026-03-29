"""Парсер кода подтверждения my.telegram.org."""
from src.mytelegram_portal.code_parse import extract_portal_confirmation_code


def test_extract_ru_sample():
    text = """
Код подтверждения для сайта. N, для Вашего аккаунта запросили код, необходимый для авторизации на my.telegram.org. Вот он:
5ZBHfYrcxNU

Не давайте этот код никому
"""
    assert extract_portal_confirmation_code(text) == "5ZBHfYrcxNU"


def test_extract_no_hint_returns_none():
    text = "Вот он:\nABCD1234EFGH\n"
    assert extract_portal_confirmation_code(text) is None


def test_extract_english_here_is():
    text = (
        "Someone requested a code for my.telegram.org. here is: Xy9AbCdEfGh12\n"
        "Ignore if you did not request."
    )
    assert extract_portal_confirmation_code(text) == "Xy9AbCdEfGh12"
