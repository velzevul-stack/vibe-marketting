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
   - `phone` — номер в международном формате с `+` и кодом страны (например +375…, +7…, +95…)

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

Справка CLI: `python main.py -h`

| Пункт | Описание |
|-------|----------|
| **1** | Поиск групп (API + groups.txt, фильтрация) |
| **2** | Сбор базы: п.1 — один аккаунт (общий без прокси / отдельный вход), п.2 — стандартный режим |
| **3** | Вступить в группы (found_groups.json) |
| **4** | Добавить в контакты |
| **5** | Пригласить в канал |
| **6** | Статистика базы |
| **7** | Просмотр найденных групп |
| **8** | Подменю: прокси (назначить, проверить), сессии Telethon, подготовка аккаунтов (2FA / сброс сессий) |
| **0** | Выход |

В главном меню пункт **9** (хаб): импорт ZIP, рассылка из пакета (`campaign/`), очистка аккаунтов/прокси, настройки, сессии `.session`, подготовка аккаунтов.

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

## Playwright (my.telegram.org / меню 9 → 4)

После `pip install -r requirements.txt` и **`playwright install chromium`** на **Linux-сервере** может понадобиться установка **системных** зависимостей браузера. Если Chromium падает с `libatk-1.0.so.0: cannot open shared object file` (или другими `*.so`):

```bash
sudo playwright install-deps
# или: python -m playwright install-deps
```

На Ubuntu при необходимости вручную: `sudo apt-get install -y libatk1.0-0 libatk-bridge2.0-0`.

Если задано `mytg_headless: false`, но на Linux **нет** `DISPLAY` / `WAYLAND_DISPLAY`, запуск **автоматически идёт в headless** (иначе Chromium падает с «Missing X server»). Чтобы оставить не-headless на сервере без монитора, оберните процесс в `xvfb-run -a …` — см. `bundles/mytelegram_api/CHAT_INSTRUCTION_RU.md`.

## Дополнительно

- **Прокси и аккаунты:** [docs/PROXY_AND_ACCOUNTS.md](PROXY_AND_ACCOUNTS.md)
- **Краткая настройка:** [config/CONFIG.md](../config/CONFIG.md)
