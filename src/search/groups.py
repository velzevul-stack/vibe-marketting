"""Поиск групп вейп-тематики."""
import asyncio
import random
import re
from pathlib import Path
from urllib.parse import unquote

import httpx

from src.config import (
    load_keywords,
    load_exclude_keywords,
    load_cities,
    load_manual_groups,
    ProxyPool,
    Settings,
)


def _has_vape_marker(text: str, markers: list[str]) -> bool:
    """Проверить наличие вейп-маркера в тексте."""
    if not text:
        return False
    text_lower = text.lower()
    return any(m.lower() in text_lower for m in markers)


def _has_exclude_keywords(text: str, keywords: list[str]) -> int:
    """Подсчитать стоп-слова. Возвращает количество совпадений."""
    if not text:
        return 0
    text_lower = text.lower()
    return sum(1 for k in keywords if k.lower() in text_lower)


def filter_vape_groups(groups: list[dict]) -> list[dict]:
    """Отфильтровать группы: исключить обычные барахолки, оставить вейп-тематику."""
    keywords = load_keywords()
    exclude_cfg = load_exclude_keywords()
    vape_markers = keywords.get("vape_markers", [])
    exclude_kw = exclude_cfg.get("generic_fleamarket", [])
    require_vape = exclude_cfg.get("vape_markers_required", True)

    result = []
    for g in groups:
        title = g.get("title", "") or ""
        desc = g.get("description", "") or ""
        combined = f"{title} {desc}"

        if require_vape and not _has_vape_marker(combined, vape_markers):
            continue
        exclude_count = _has_exclude_keywords(combined, exclude_kw)
        if exclude_count >= 2 and not _has_vape_marker(combined, vape_markers):
            continue
        g["relevance_score"] = sum(1 for m in vape_markers if m.lower() in combined.lower()) - exclude_count * 0.5
        result.append(g)
    return result


async def search_telegram_index(
    query: str, api_key: str, page: int = 1, proxy: str | None = None
) -> list[dict]:
    """Поиск через Telegram Index API (RapidAPI). С поддержкой прокси."""
    if not api_key:
        return []
    url = "https://telegram-index-api.p.rapidapi.com/search"
    headers = {"x-rapidapi-key": api_key, "x-rapidapi-host": "telegram-index-api.p.rapidapi.com"}
    params = {"query": query, "type": "group", "page": page, "sort": "rlvn"}
    async with httpx.AsyncClient(timeout=30, proxy=proxy) as client:
        try:
            r = await client.get(url, headers=headers, params=params)
            data = r.json()
            results = data.get("results", [])
            return [
                {
                    "id": str(g.get("id", "")),
                    "title": g.get("title", ""),
                    "link": g.get("link", ""),
                    "members": g.get("members", 0),
                    "description": g.get("description", ""),
                    "source": "telegram_index",
                }
                for g in results
            ]
        except Exception:
            return []


def load_manual_groups_as_list() -> list[dict]:
    """Преобразовать ручной список в формат групп."""
    links = load_manual_groups()
    result = []
    for link in links:
        username = _extract_username_from_link(link)
        result.append({
            "id": username or link,
            "title": username or "Manual",
            "link": link,
            "members": 0,
            "description": "",
            "source": "manual",
        })
    return result


def _extract_username_from_link(link: str) -> str | None:
    """Извлечь username из ссылки t.me."""
    m = re.search(r"t\.me/([a-zA-Z0-9_]+)", link)
    return m.group(1) if m else None


def _normalize_group_key(link: str | None, group_id: str | None = None) -> str:
    """
    Канонический ключ для дедупликации: t.me/username (lowercase).
    Исключает дубли из разных источников (RapidAPI, TGStat, Telemetr, DDGS, tg-cat).
    """
    if link:
        m = re.search(r"t\.me/([a-zA-Z0-9_-]+)", link, re.I)
        if m:
            return f"t.me/{m.group(1).lower()}"
    if group_id:
        clean = str(group_id).strip().lstrip("@").lower()
        if clean and re.match(r"^[a-zA-Z0-9_-]+$", clean):
            return f"t.me/{clean}"
    return (link or group_id or "").lower()


def _extract_tme_links_from_ddgs_results(results: list[dict]) -> list[dict]:
    """Извлечь t.me ссылки из результатов DuckDuckGo."""
    seen: set[str] = set()
    groups: list[dict] = []
    for r in results:
        href = (r.get("href") or r.get("url") or "").strip()
        title = (r.get("title") or "").strip()
        if not href:
            continue
        # Декодируем URL (реддиректы могут содержать закодированный target)
        href_decoded = unquote(href)
        # Ищем t.me/username, t.me/s/username (preview), t.me/joinchat/hash
        for match in re.finditer(r"t\.me/(?:s/)?([a-zA-Z0-9_-]+)(?:/([a-zA-Z0-9_-]+))?", href_decoded):
            slug = match.group(1)
            sub = match.group(2) or ""
            if slug.lower() in ("share", "proxy"):
                continue
            # t.me/s/xxx -> используем xxx как username
            link = f"https://t.me/{slug}" + (f"/{sub}" if sub else "")
            if link in seen:
                continue
            seen.add(link)
            groups.append({
                "id": slug,
                "title": title or slug,
                "link": link,
                "members": 0,
                "description": "",
                "source": "ddgs",
            })
    return groups


def _search_ddgs_sync(query: str, proxy: str | None = None, max_results: int = 15) -> list[dict]:
    """Синхронный поиск через DuckDuckGo (ddgs). Бесплатно, без API-ключа."""
    try:
        from ddgs import DDGS
        ddgs = DDGS(proxy=proxy, timeout=15) if proxy else DDGS(timeout=15)
        results = list(ddgs.text(query, max_results=max_results, region="ru-ru"))
        return _extract_tme_links_from_ddgs_results(results)
    except Exception:
        return []


async def search_via_ddgs(
    query: str, proxy: str | None = None, max_results: int = 15
) -> list[dict]:
    """Поиск через DuckDuckGo (ddgs). Бесплатно, без API-ключа. С поддержкой прокси."""
    return await asyncio.to_thread(_search_ddgs_sync, query, proxy, max_results)


TG_CATALOG_BASE = "https://tg-cat.com"
TG_CATALOG_USERNAME_RE = re.compile(r"tg-cat\.com/@([a-zA-Z0-9_]+)", re.I)


async def search_tgstat_api(
    query: str, token: str, proxy: str | None = None, country: str = "by"
) -> list[dict]:
    """
    Поиск через TGStat API (api.tgstat.ru).
    Требует токен, платный (API Stat S+). 2.9M+ каналов и чатов.
    """
    if not token or len(query.strip()) < 3:
        return []
    url = "https://api.tgstat.ru/channels/search"
    params = {
        "token": token,
        "q": query.strip(),
        "peer_type": "chat",
        "country": country,
        "limit": 100,
    }
    groups: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=30, proxy=proxy) as client:
            r = await client.get(url, params=params)
            data = r.json()
        if data.get("status") != "ok":
            return []
        items = data.get("response", {}).get("items", [])
        for ch in items:
            link = ch.get("link", "") or ""
            username = (ch.get("username") or "").lstrip("@")
            if not link and username:
                link = f"https://t.me/{username}"
            if not link:
                continue
            if not link.startswith("http"):
                link = f"https://{link}"
            groups.append({
                "id": username or link.split("/")[-1],
                "title": ch.get("title", "") or username,
                "link": link,
                "members": ch.get("participants_count", 0),
                "description": ch.get("about", ""),
                "source": "tgstat",
            })
    except Exception:
        pass
    return groups


async def search_telemetr_api(
    query: str, api_key: str, proxy: str | None = None
) -> list[dict]:
    """
    Поиск через Telemetr API (api.telemetr.io).
    Free: 1000 req/мес. 1.8M+ каналов.
    Два запроса: search → info-batch (для получения link).
    """
    if not api_key or not query.strip():
        return []
    headers = {"x-api-key": api_key}
    groups: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=30, proxy=proxy) as client:
            r = await client.get(
                "https://api.telemetr.io/v1/channels/search",
                params={"term": query.strip(), "limit": 50},
                headers=headers,
            )
            items = r.json()
            if not isinstance(items, list):
                items = items.get("channels", items.get("items", []))
            internal_ids = [ch.get("internal_id") for ch in items if ch.get("internal_id")]
            if not internal_ids:
                return []
            ids_str = ",".join(internal_ids[:100])
            r2 = await client.get(
                "https://api.telemetr.io/v1/channels/info-batch",
                params={"ids": ids_str},
                headers=headers,
            )
            batch = r2.json()
        channels = batch.get("channels", [])
        for ch in channels:
            link = ch.get("link", "") or ""
            if not link or "t.me" not in link:
                continue
            if not link.startswith("http"):
                link = f"https://{link}"
            username = link.split("t.me/")[-1].split("/")[0].split("?")[0]
            groups.append({
                "id": username,
                "title": ch.get("title", "") or username,
                "link": link,
                "members": ch.get("members_count", 0),
                "description": ch.get("description", ""),
                "source": "telemetr",
            })
    except Exception:
        pass
    return groups


async def search_tg_catalog(
    query: str, proxy: str | None = None
) -> list[dict]:
    """
    Поиск через каталог TG Catalog (tg-cat.com).
    Бесплатно, без API-ключа. Парсит страницу поиска.
    """
    url = f"{TG_CATALOG_BASE}/"
    params = {"search": query, "type": "supergroup"}
    seen: set[str] = set()
    groups: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=25, proxy=proxy, follow_redirects=True) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            html = r.text
        for m in TG_CATALOG_USERNAME_RE.finditer(html):
            username = m.group(1).lower()
            if username in seen:
                continue
            seen.add(username)
            link = f"https://t.me/{username}"
            groups.append({
                "id": username,
                "title": username,
                "link": link,
                "members": 0,
                "description": "",
                "source": "tg_catalog",
            })
    except Exception:
        pass
    return groups


async def search_groups(
    api_key: str | None = None,
    proxy_pool: ProxyPool | None = None,
    use_ddgs: bool | None = None,
    use_tg_catalog: bool | None = None,
    on_progress: "callable[[str, str, int, int, int, str], None] | None" = None,
) -> list[dict]:
    """
    Поиск групп: RapidAPI + DuckDuckGo + TG Catalog + ручной список.
    on_progress(source, query, current, total, found, proxy_info) — вызывается при прогрессе.
    """
    all_groups: dict[str, dict] = {}
    pool = proxy_pool or ProxyPool()
    settings = Settings()
    ddgs_enabled = use_ddgs if use_ddgs is not None else settings.ddgs_search_enabled
    tg_catalog_enabled = use_tg_catalog if use_tg_catalog is not None else settings.tg_catalog_enabled

    def _add_groups(groups: list[dict]) -> None:
        for g in groups:
            key = _normalize_group_key(g.get("link"), g.get("id"))
            if key and key not in all_groups:
                all_groups[key] = g

    def _report(source: str, query: str, cur: int, total: int, proxy_info: str = "") -> None:
        if on_progress:
            on_progress(source, query, cur, total, len(all_groups), proxy_info)

    # Ручной список
    _add_groups(load_manual_groups_as_list())
    if on_progress:
        on_progress("manual", "groups.txt", 1, 1, len(all_groups), "")

    keywords = load_keywords()
    themes = keywords.get("search_themes", ["vape барахолка", "вейп барахолка", "парилка"])
    cities = load_cities()

    async def _run_with_progress(
        source: str,
        queries: list[str],
        search_fn,
    ) -> None:
        total = len(queries)
        search_min = getattr(settings, "delay_search_min", 2.0)
        search_max = getattr(settings, "delay_search_max", 6.0)
        for i, q in enumerate(queries):
            proxy, proxy_info = pool.get_next_with_info() if pool.proxies else (None, "—")
            grp = await search_fn(q, proxy)
            _add_groups(grp)
            _report(source, q, i + 1, total, proxy_info)
            if i < total - 1:
                delay = random.uniform(search_min, search_max)
                await asyncio.sleep(delay)

    # Telegram Index (RapidAPI) — по городам и темам
    if api_key:
        queries_ti = [f"{theme} {city}" for theme in themes for city in cities]
        if queries_ti:
            async def _do_ti(q: str, proxy: str | None):
                return await search_telegram_index(q, api_key, proxy=proxy)

            await _run_with_progress("RapidAPI", queries_ti, _do_ti)

    # TGStat API — платный
    tgstat_token = settings.tgstat_token
    if tgstat_token:
        tgstat_queries = list(dict.fromkeys(themes[:5] + ["vape", "вейп"]))[:8]
        async def _search_tgstat(q: str, proxy: str | None):
            return await search_tgstat_api(q, tgstat_token, proxy=proxy)

        await _run_with_progress("TGStat", tgstat_queries, _search_tgstat)

    # Telemetr API
    telemetr_key = settings.telemetr_api_key
    if telemetr_key:
        telemetr_queries = list(dict.fromkeys(themes[:5] + ["vape", "вейп"]))[:8]
        async def _search_telemetr(q: str, proxy: str | None):
            return await search_telemetr_api(q, telemetr_key, proxy=proxy)

        await _run_with_progress("Telemetr", telemetr_queries, _search_telemetr)

    # TG Catalog (tg-cat.com) — бесплатно
    if tg_catalog_enabled:
        catalog_queries = list(dict.fromkeys(
            themes[:5] + ["vape", "вейп", "парилка", "барахолка вейп"]
        ))[:10]
        async def _search_catalog(q: str, proxy: str | None):
            return await search_tg_catalog(q, proxy=proxy)

        await _run_with_progress("TG Catalog", catalog_queries, _search_catalog)

    # DuckDuckGo — бесплатный поиск (site:t.me + темы)
    if ddgs_enabled:
        ddgs_queries = [f"site:t.me {theme}" for theme in themes[:5]]
        ddgs_queries += [f"site:t.me {theme} {city}" for theme in themes[:3] for city in cities[:8]]
        ddgs_queries = ddgs_queries[:15]
        if ddgs_queries:
            async def _search_ddgs(q: str, proxy: str | None):
                return await search_via_ddgs(q, proxy=proxy, max_results=15)

            await _run_with_progress("DuckDuckGo", ddgs_queries, _search_ddgs)

    groups_list = list(all_groups.values())
    return filter_vape_groups(groups_list)
