from src.mytelegram_portal.runner import phone_e164_from_session_stem


def test_stem_plain():
    assert phone_e164_from_session_stem("375291234567") == "+375291234567"


def test_stem_with_plus_prefix():
    assert phone_e164_from_session_stem("+123456789012") == "+123456789012"


def test_stem_plus_and_collision_suffix():
    assert phone_e164_from_session_stem("+375291234567_a1b2") == "+375291234567"


def test_stem_with_collision_suffix():
    assert phone_e164_from_session_stem("375291234567_a1b2") == "+375291234567"


def test_stem_too_short():
    assert phone_e164_from_session_stem("1234567") is None


def test_stem_non_digit():
    assert phone_e164_from_session_stem("account1") is None


def test_stem_tg_prefix():
    assert phone_e164_from_session_stem("tg_abcd12") is None
