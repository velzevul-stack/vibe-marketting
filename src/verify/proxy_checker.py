"""Проверка работоспособности прокси."""
import asyncio
import time
from dataclasses import dataclass

import httpx

# URL для проверки — быстрый и стабильный
CHECK_URL = "https://api.telegram.org"
CHECK_TIMEOUT = 15.0


@dataclass
class ProxyResult:
    """Результат проверки одного прокси."""
    proxy: str
    ok: bool
    latency_ms: float | None
    error: str | None


async def check_proxy(proxy: str, timeout: float = CHECK_TIMEOUT) -> ProxyResult:
    """Проверить один прокси. Возвращает ProxyResult."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=timeout) as client:
            r = await client.get(CHECK_URL)
            r.raise_for_status()
        elapsed_ms = (time.monotonic() - start) * 1000
        return ProxyResult(proxy=proxy, ok=True, latency_ms=elapsed_ms, error=None)
    except Exception as e:
        err_msg = str(e)
        if len(err_msg) > 80:
            err_msg = err_msg[:77] + "..."
        return ProxyResult(proxy=proxy, ok=False, latency_ms=None, error=err_msg)


async def check_proxies(
    proxies: list[str],
    max_concurrent: int = 10,
    timeout: float = CHECK_TIMEOUT,
) -> list[ProxyResult]:
    """Проверить список прокси параллельно. Возвращает список ProxyResult."""
    sem = asyncio.Semaphore(max_concurrent)

    async def _check(p: str) -> ProxyResult:
        async with sem:
            return await check_proxy(p, timeout=timeout)

    return await asyncio.gather(*[_check(p) for p in proxies])
