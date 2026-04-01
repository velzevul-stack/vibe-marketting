"""Профили Playwright для mytg: стабильность и режим legacy."""
from __future__ import annotations

from src.config import Settings
from src.mytelegram_portal.browser_profiles import playwright_context_options_for_mytg


def test_mytg_context_stable_across_calls() -> None:
    s = Settings(data={"mytg_diverse_contexts": True})
    a = playwright_context_options_for_mytg("+123456789012", s)
    b = playwright_context_options_for_mytg("+123456789012", s)
    assert a == b
    assert a["user_agent"] == b["user_agent"]
    assert a["timezone_id"] == b["timezone_id"]


def test_mytg_legacy_mode_two_user_agents_only() -> None:
    s = Settings(data={"mytg_diverse_contexts": False})
    uas = {
        playwright_context_options_for_mytg(f"acc{i}", s)["user_agent"] for i in range(20)
    }
    assert len(uas) <= 2
    assert all("Chrome/" in ua for ua in uas)
    for i in range(20):
        o = playwright_context_options_for_mytg(f"acc{i}", s)
        assert o["viewport"] == {"width": 1280, "height": 800}
