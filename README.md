# Vibe Marketing CLI

Консольное приложение для вайб-маркетинга: поиск продавцов жидкостей в Telegram-группах вейп-тематики по городам Беларуси и приглашение в закрытый канал.

## Quick Start

```bash
pip install -r requirements.txt
cp config/settings.json.example config/settings.json
cp config/accounts.json.example config/accounts.json
# Заполните api_id, api_hash, phone в config/accounts.json (см. my.telegram.org)
python main.py
```

Подробная инструкция: [docs/INSTALLATION.md](docs/INSTALLATION.md)

## Установка

```bash
cd vibe-marketing-cli
pip install -r requirements.txt
```

## Настройка

Подробнее: [docs/PROXY_AND_ACCOUNTS.md](docs/PROXY_AND_ACCOUNTS.md), [config/CONFIG.md](config/CONFIG.md).

1. **settings.json** — скопируйте `config/settings.json.example` в `config/settings.json`:
   - `telegram_index_api_key` — опционально, для поиска через Telegram Index (RapidAPI)
   - `proxies` — укажите файлы с прокси (`files`) или список (`list`)

2. **accounts.json** — скопируйте `config/accounts.json.example` в `config/accounts.json`:
   - `api_id`, `api_hash` — с [my.telegram.org](https://my.telegram.org)
   - `phone` — номер телефона
   - `proxy` — прокси для аккаунта (опционально)

3. **groups.txt** — добавьте ссылки на группы (по одной на строку)

## Запуск

```bash
python main.py
```

## Меню

- **Поиск групп** — поиск через Telegram Index API + ручной список, фильтрация обычных барахолок
- **Сбор базы** — парсинг сообщений в группах, извлечение продавцов (горячие/тёплые)
- **Вступить в группы** — вступление в группы из found_groups.json (публичные и приватные по invite-ссылке)
- **Добавить в контакты** — добавление пользователей в контакты с умным распределением
- **Пригласить в канал** — добавление в канал напрямую из контактов аккаунта
- **Статистика** — подсчёт пользователей в базе

## Прокси

- **Поиск** и **сбор базы** — используют прокси из пула (round-robin)
- **Назначить прокси аккаунтам** (п.7) — перераспределяет прокси из пула по аккаунтам для join/add/invite

## Умное распределение

- **Least-used-first** — выбор аккаунта с наименьшей нагрузкой
- **FloodWait-aware** — исключение аккаунтов в FloodWait до истечения таймера
- **Взвешенная ротация** — не использовать один аккаунт дважды подряд

## Структура

```
output/
├── vibe_marketing.db    # SQLite база
├── found_groups.json   # Найденные группы
├── checkpoint.json     # Чекпоинт для продолжения
└── users_*.json        # Экспорт (опционально)
```
