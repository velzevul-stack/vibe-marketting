# Установка и запуск Vibe Marketing CLI

## Требования

- **Python 3.10+**
- **pip**

## Установка

```bash
cd vibe-marketing-cli
pip install -r requirements.txt
```

## Настройка перед первым запуском

1. Скопируйте примеры конфигов:
   ```bash
   cp config/settings.json.example config/settings.json
   cp config/accounts.json.example config/accounts.json
   ```

2. Заполните `config/accounts.json`:
   - `api_id`, `api_hash` — получите на [my.telegram.org](https://my.telegram.org)
   - `phone` — номер в формате +375291234567

3. (Опционально) Настройте прокси — см. [PROXY_AND_ACCOUNTS.md](PROXY_AND_ACCOUNTS.md)

4. (Опционально) Добавьте группы в `config/groups.txt` — по одной ссылке на строку

## Первый запуск и авторизация

```bash
python main.py
```

При первом запуске для каждого аккаунта из `accounts.json` потребуется:
- Ввести код из Telegram (придёт в приложение)
- При необходимости — 2FA пароль

Сессии сохраняются в `sessions/` и повторная авторизация не требуется.

## Меню

| Пункт | Описание |
|-------|----------|
| **1. Поиск групп** | Поиск через Telegram Index API + ручной список из groups.txt, фильтрация барахолок |
| **2. Сбор базы** | Парсинг сообщений в группах, извлечение продавцов (горячие/тёплые) |
| **3. Вступить в группы** | Вступление в группы из found_groups.json (публичные и приватные по invite-ссылке) |
| **4. Добавить в контакты** | Добавление пользователей в контакты с умным распределением по аккаунтам |
| **5. Пригласить в канал** | Приглашение в канал напрямую из контактов аккаунта |
| **6. Статистика** | Подсчёт пользователей в базе |
| **7. Назначить прокси аккаунтам** | Перераспределение прокси из пула по аккаунтам для join/add/invite |

## Запуск на сервере (screen/tmux)

Для интерактивного использования в фоне:

```bash
# screen
screen -S vibe
cd /path/to/vibe-marketing-cli
python main.py
# Ctrl+A, D — отключиться
# screen -r vibe — вернуться

# tmux
tmux new -s vibe
cd /path/to/vibe-marketing-cli
python main.py
# Ctrl+B, D — отключиться
# tmux attach -t vibe — вернуться
```

## Структура каталогов

```
vibe-marketing-cli/
├── output/
│   ├── vibe_marketing.db    # SQLite база (продавцы)
│   ├── found_groups.json   # Найденные группы
│   ├── checkpoint.json     # Чекпоинт для продолжения
│   └── users_*.json        # Экспорт (опционально)
├── sessions/               # Сессии Telegram (авторизация)
├── config/                 # Настройки (см. PROXY_AND_ACCOUNTS.md)
└── data/                   # Данные (города и т.д.)
```

## Дополнительно

- **Прокси и аккаунты:** [docs/PROXY_AND_ACCOUNTS.md](PROXY_AND_ACCOUNTS.md)
- **Краткая настройка:** [config/CONFIG.md](../config/CONFIG.md)
