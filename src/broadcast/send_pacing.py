"""Паузы между отправками: глобально между аккаунтами и (для CSV) ещё минимум на аккаунт."""
from __future__ import annotations

import asyncio
import time


class GlobalAccountSendGap:
    """Минимум секунд между успешными отправками (любые сессии), плюс стартовый сдвиг по индексу воркера."""

    def __init__(self, gap_sec: float) -> None:
        self.gap_sec = max(0.0, float(gap_sec))
        self._lock = asyncio.Lock()
        self._last_success_mono: float | None = None

    async def wait_my_turn(self) -> None:
        if self.gap_sec <= 0:
            return
        while True:
            async with self._lock:
                now = time.monotonic()
                if self._last_success_mono is None:
                    return
                need = self._last_success_mono + self.gap_sec - now
            if need < 0.05:
                return
            await asyncio.sleep(need)

    async def mark_success(self) -> None:
        if self.gap_sec <= 0:
            return
        async with self._lock:
            self._last_success_mono = time.monotonic()


class CsvSendPacer:
    """
    Для CSV: ``own_interval_sec`` между отправками одной сессии,
    ``account_gap_sec`` между любыми двумя успешными отправками (разные или те же аккаунты).
    """

    def __init__(self, own_interval_sec: float, account_gap_sec: float) -> None:
        self.own_interval_sec = max(0.0, float(own_interval_sec))
        self.account_gap_sec = max(0.0, float(account_gap_sec))
        self._lock = asyncio.Lock()
        self._last_global: float | None = None
        self._per_session: dict[str, float] = {}

    def mark_session_ready_after_precache(
        self, session_name: str, *, stagger_index: int = 0
    ) -> None:
        """
        Старт отсчёта own_interval до первой отправки.
        ``stagger_index * account_gap_sec`` сдвигает первое окно этой сессии (чтобы не слали пачкой).
        """
        base = time.monotonic()
        si = max(0, int(stagger_index))
        shift = si * self.account_gap_sec
        self._per_session[session_name] = base - self.own_interval_sec + shift

    async def wait_before_send(self, session_name: str) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                own_last = self._per_session.get(session_name, now)
                w_own = max(0.0, own_last + self.own_interval_sec - now)
                w_glob = 0.0
                if self.account_gap_sec > 0 and self._last_global is not None:
                    w_glob = max(0.0, self._last_global + self.account_gap_sec - now)
                wait = max(w_own, w_glob)
            if wait < 0.05:
                return
            await asyncio.sleep(wait)

    async def mark_sent(self, session_name: str) -> None:
        async with self._lock:
            t = time.monotonic()
            self._last_global = t
            self._per_session[session_name] = t
