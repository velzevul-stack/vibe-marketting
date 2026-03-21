# Vibe Marketing CLI

Консольное приложение для вайб-маркетинга: поиск продавцов жидкостей в Telegram-группах вейп-тематики по городам Беларуси и приглашение в закрытый канал.

## Quick Start

```bash
pip install -r requirements.txt
cp config/settings.json.example config/settings.json
cp config/accounts.json.example config/accounts.json
# Заполните api_id, api_hash, phone в config/accounts.json (см. my.telegram.org)
python main.py
python main.py -h   # справка по флагам (--assign-proxies и т.д.)
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
   - `telegram_index_api_key` — RapidAPI (Telegram Index)
   - `tgstat_token` — TGStat API (api.tgstat.ru)
   - `telemetr_api_key` — Telemetr API (api.telemetr.io, free 1000 req/мес)
   - `ddgs_search_enabled` — DuckDuckGo (по умолчанию true)
   - `tg_catalog_enabled` — TG Catalog tg-cat.com (по умолчанию true)
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

## Меню (пункты **1–9** по порядку, затем **a**; **0** — выход)

| Ключ | Действие |
|------|----------|
| **1** | Поиск групп (RapidAPI, TGStat, Telemetr, TG Catalog, DuckDuckGo, groups.txt) |
| **2** | Найденные группы: просмотр, **экспорт в .txt** и **импорт из .txt** (`found_groups.json`) |
| **3** | Статистика базы |
| **4** | База пользователей (SQLite): поиск по username, просмотр порциями |
| **5** | Сбор базы (подменю: один аккаунт или стандартный режим) |
| **6** | Вступить в группы |
| **7** | Добавить в контакты |
| **8** | Пригласить в канал |
| **9** | Хаб: импорт ZIP, настройки, сессии, API (заглушка), подготовка аккаунтов |
| **a** | Очистить `found_groups.json` |

Внутри хаба **9**: **1** импорт ZIP, **2** настройки, **3** сессии, **4** API, **5** bulk prepare.

## Прокси

- **Поиск** и **сбор базы** — прокси из пула (round-robin)
- **9 → 2** в меню — прокси и `telethon_default_api`; **9 → 3** — сессии Telethon

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
