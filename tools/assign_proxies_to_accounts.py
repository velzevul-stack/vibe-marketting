#!/usr/bin/env python3
"""
Назначить прокси из пула аккаунтам.
Обновляет config/accounts.json — добавляет proxy каждому аккаунту по round-robin.
Прокси, использованные для поиска и сбора базы, можно перераспределить под TG-аккаунты.
"""
import json
import sys
from pathlib import Path

# Добавить корень проекта
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_accounts, load_proxies


def main() -> None:
    accounts = load_accounts()
    proxies = load_proxies()

    if not accounts:
        print("Нет аккаунтов в config/accounts.json")
        return
    if not proxies:
        print("Нет прокси. Добавьте в config/proxies.txt или settings.json")
        return

    for i, acc in enumerate(accounts):
        proxy = proxies[i % len(proxies)]
        acc["proxy"] = proxy

    path = Path(__file__).parent.parent / "config" / "accounts.json"
    path.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Назначено {len(proxies)} прокси для {len(accounts)} аккаунтов")
    print(f"Сохранено в {path}")


if __name__ == "__main__":
    main()
