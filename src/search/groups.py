"""Поиск групп вейп-тематики."""
import asyncio
import re
from pathlib import Path

import httpx

from src.config import load_keywords, load_exclude_keywords, load_cities, load_manual_groups, ProxyPool


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


async def search_groups(
    api_key: str | None = None, proxy_pool: ProxyPool | None = None
) -> list[dict]:
    """Поиск групп: Telegram Index + ручной список. С прокси для API."""
    all_groups: dict[str, dict] = {}
    pool = proxy_pool or ProxyPool()

    # Ручной список
    for g in load_manual_groups_as_list():
        key = g.get("link") or g.get("id", "")
        if key and key not in all_groups:
            all_groups[key] = g

    # Telegram Index по городам и темам (с прокси, параллельно)
    if api_key:
        keywords = load_keywords()
        themes = keywords.get("search_themes", ["vape барахолка", "вейп барахолка", "парилка"])
        cities = load_cities()

        queries = [f"{theme} {city}" for theme in themes for city in cities]
        if queries:
            async def _search_one(q: str):
                proxy = pool.get_next() if pool.proxies else None
                return await search_telegram_index(q, api_key, proxy=proxy)

            results = await asyncio.gather(*[_search_one(q) for q in queries])
            for groups in results:
                for g in groups:
                    key = g.get("link") or g.get("id", "")
                    if key and key not in all_groups:
                        all_groups[key] = g

    groups_list = list(all_groups.values())
    return filter_vape_groups(groups_list)
