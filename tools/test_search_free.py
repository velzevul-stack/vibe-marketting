#!/usr/bin/env python3
"""Тест поиска без API — только бесплатные источники."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from collections import Counter

from src.search.groups import search_groups


def main():
    print("Поиск без API (только TG Catalog + DuckDuckGo + groups.txt)...")
    print()
    groups = asyncio.run(search_groups(api_key=None))
    print(f"Найдено групп: {len(groups)}")
    print()
    print("По источникам:")
    sources = Counter(g.get("source", "?") for g in groups)
    for src, cnt in sources.most_common():
        print(f"  {src}: {cnt}")
    print()
    print("Примеры (первые 20):")
    for g in groups[:20]:
        title = (g.get("title") or "")[:35]
        print(f"  {g['source']:12} | {g['link']:45} | {title}")
    print()
    print("Сохранено в output/found_groups.json")
    from pathlib import Path
    import json
    out = Path("output") / "found_groups.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
