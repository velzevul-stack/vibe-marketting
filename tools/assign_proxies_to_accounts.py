#!/usr/bin/env python3
"""
Назначить прокси из пула аккаунтам.
Обновляет config/accounts.json — добавляет proxy каждому аккаунту по round-robin.
Прокси, использованные для поиска и сбора базы, можно перераспределить под TG-аккаунты.
"""
import sys
from pathlib import Path

# Добавить корень проекта
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import assign_proxies_round_robin_to_accounts, load_accounts, load_proxies


def main() -> None:
    if not load_accounts():
        print("Нет аккаунтов в config/accounts.json")
        return
    if not load_proxies():
        print("Нет прокси. Добавьте в config/proxies.txt или settings.json")
        return

    ok, msg = assign_proxies_round_robin_to_accounts()
    if ok:
        print(f"Назначено прокси, сохранено в {msg}")
    else:
        print(msg)


if __name__ == "__main__":
    main()
