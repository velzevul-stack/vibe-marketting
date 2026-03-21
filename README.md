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

## Меню

- **1** Поиск групп — RapidAPI + TGStat + Telemetr + TG Catalog + DuckDuckGo + groups.txt, без дублей
- **7** Просмотр найденных групп, **6** статистика
- **b** База пользователей (SQLite) — поиск по подстроке username, просмотр порциями по 20
- **2** Сбор базы — подменю: один аккаунт (общий без прокси или отдельный вход в консоли + опция прокси) либо стандартный режим из settings
- **3** Вступить в группы — из found_groups.json или txt со ссылками
- **4** Добавить в контакты, **5** пригласить в канал
- **8** Импорты, настройки и аккаунты (хаб): **1** импорт ZIP (пары `.json` + `.session`), **2** настройки (прокси, `telethon_default_api`, синхронизация сессий), **3** меню сессий Telethon, **4** справка по API my.telegram.org (опционально), **5** подготовка аккаунтов
- **9** Очистить `found_groups.json`, **a** фильтр базы по признакам РБ

## Прокси

- **Поиск** и **сбор базы** — используют прокси из пула (round-robin)
- **Меню 8 → 2** — включение/выключение прокси, назначение аккаунтам, проверка пула; **8 → 3** — сессии Telethon

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
