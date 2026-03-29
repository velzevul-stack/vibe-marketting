"""Паузы между отправками: по группам API и (для CSV) ещё минимум на сессию."""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Mapping


class GlobalAccountSendGap:
    """
    Минимум секунд между успешными отправками **внутри одной группы API**
    (``api_id`` + ``api_hash`` из accounts.json). У разных приложений — независимые очереди.
    """

    def __init__(self, gap_sec: float) -> None:
        self.gap_sec = max(0.0, float(gap_sec))
        self._lock = asyncio.Lock()
        self._last_success_mono: dict[str, float] = {}

    async def wait_my_turn(self, api_group_key: str) -> None:
        if self.gap_sec <= 0:
            return
        key = str(api_group_key or "").strip() or "_default"
        while True:
            async with self._lock:
                now = time.monotonic()
                last = self._last_success_mono.get(key)
                if last is None:
                    return
                need = last + self.gap_sec - now
            if need < 0.05:
                return
            await asyncio.sleep(need)

    async def mark_success(self, api_group_key: str) -> None:
        if self.gap_sec <= 0:
            return
        key = str(api_group_key or "").strip() or "_default"
        async with self._lock:
            self._last_success_mono[key] = time.monotonic()


class CsvSendPacer:
    """
    Для CSV: пауза между **попытками к получателю** одной сессии (OK, skip, fail после resolve),
    и между такими попытками **в пределах одной группы API** — отдельный интервал (фикс. или случайный).
    """

    def __init__(
        self,
        own_interval_sec: float,
        account_gap_sec: float,
        *,
        own_interval_max_sec: float | None = None,
        account_gap_max_sec: float | None = None,
        session_api_group: Mapping[str, str] | None = None,
    ) -> None:
        self._own_min = max(0.0, float(own_interval_sec))
        self._own_max = (
            None
            if own_interval_max_sec is None
            else max(self._own_min, float(own_interval_max_sec))
        )
        self._gap_min = max(0.0, float(account_gap_sec))
        self._gap_max = (
            None
            if account_gap_max_sec is None
            else max(self._gap_min, float(account_gap_max_sec))
        )
        self._session_api = dict(session_api_group or {})
        self._lock = asyncio.Lock()
        self._deadline_glob: dict[str, float | None] = {}
        self._deadline_own: dict[str, float] = {}

    def _api_key(self, session_name: str) -> str:
        sn = (session_name or "").strip()
        if sn in self._session_api:
            return self._session_api[sn]
        return f"_unknown:{sn or '?'}"

    def _stagger_base_sec(self) -> float:
        """Сдвиг старта воркеров внутри группы API: по минимальному зазору группы."""
        return self._gap_min

    def _sample_own(self) -> float:
        if self._own_max is None:
            return self._own_min
        return random.uniform(self._own_min, self._own_max)

    def _sample_gap(self) -> float:
        if self._gap_max is None:
            return self._gap_min
        return random.uniform(self._gap_min, self._gap_max)

    async def wait_after_connect_before_broadcast(self) -> float:
        """
        Полная пауза «интервала сессии`` после успешного входа, до precache и рассылки.
        Возвращает фактически выдержанные секунды (для лога).
        """
        sec = self._sample_own()
        if sec >= 0.05:
            await asyncio.sleep(sec)
        return sec

    def mark_session_ready_after_precache(
        self, session_name: str, *, stagger_index: int = 0
    ) -> None:
        """
        После precache: первое ``wait_before_send`` учитывает только сдвиг внутри API-группы
        (полная пауза уже была после входа — ``wait_after_connect_before_broadcast``).
        """
        base = time.monotonic()
        si = max(0, int(stagger_index))
        shift = si * self._stagger_base_sec()
        self._deadline_own[session_name] = base + shift

    async def wait_before_send(self, session_name: str) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                d_o = self._deadline_own.get(session_name, now)
                ak = self._api_key(session_name)
                d_g = self._deadline_glob.get(ak)
                need_o = max(0.0, d_o - now)
                need_g = max(0.0, d_g - now) if d_g is not None else 0.0
                wait = max(need_o, need_g)
            if wait < 0.05:
                return
            await asyncio.sleep(wait)

    async def mark_recipient_attempt_done(self, session_name: str) -> None:
        """
        После успешной отправки, skip (нет peer / RPC) или окончательного fail —
        сдвинуть паузы сессии и API-группы (чтобы не долбить Telegram подряд при серии skip).
        """
        async with self._lock:
            t = time.monotonic()
            self._deadline_own[session_name] = t + self._sample_own()
            ak = self._api_key(session_name)
            self._deadline_glob[ak] = t + self._sample_gap()

    async def mark_sent(self, session_name: str) -> None:
        """Совместимость: то же, что ``mark_recipient_attempt_done``."""
        await self.mark_recipient_attempt_done(session_name)
