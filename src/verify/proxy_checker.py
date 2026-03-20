"""Проверка работоспособности прокси."""
import asyncio
import time
from dataclasses import dataclass

import httpx

CHECK_TIMEOUT = 15.0

# Несколько эндпоинтов: у разных хостов разные правила (WAF, блок ДЦ, гео).
# Раньше был только api.telegram.org + raise_for_status() — корень API часто отдаёт 404,
# из‑за этого рабочий прокси помечался как мёртвый.
_CHECK_URLS = [
    "https://api.ipify.org?format=json",
    "https://jsonip.com",
    "https://httpbin.org/ip",
    "https://api.telegram.org/",
]


def _response_ok(url: str, status: int) -> bool:
    if status == 407:
        return False  # прокси требует другую авторизацию
    if 200 <= status < 300:
        return True
    # Достучались до Telegram по HTTPS — для прокси это успех (часто 404 на корне)
    if "api.telegram.org" in url and status in (404, 422):
        return True
    return False


@dataclass
class ProxyResult:
    """Результат проверки одного прокси."""
    proxy: str
    ok: bool
    latency_ms: float | None
    error: str | None
    check_url: str | None = None  # какой URL сработал (для отладки)


async def check_proxy(proxy: str, timeout: float = CHECK_TIMEOUT) -> ProxyResult:
    """Проверить один прокси по цепочке URL. Возвращает ProxyResult."""
    start = time.monotonic()
    last_err = "нет ответа"
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=timeout, follow_redirects=True) as client:
            for url in _CHECK_URLS:
                try:
                    t0 = time.monotonic()
                    r = await client.get(url)
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    if _response_ok(url, r.status_code):
                        total_ms = (time.monotonic() - start) * 1000
                        return ProxyResult(
                            proxy=proxy,
                            ok=True,
                            latency_ms=round(total_ms, 1),
                            error=None,
                            check_url=url,
                        )
                    last_err = f"{url} → HTTP {r.status_code}"
                except Exception as e:
                    last_err = f"{url}: {e}"
                    continue
    except Exception as e:
        last_err = str(e)

    err_msg = last_err
    if len(err_msg) > 120:
        err_msg = err_msg[:117] + "..."
    return ProxyResult(proxy=proxy, ok=False, latency_ms=None, error=err_msg, check_url=None)


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
