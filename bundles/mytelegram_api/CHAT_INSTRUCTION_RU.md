# Получение api_id / api_hash (my.telegram.org) — краткая инструкция

## Что делает скрипт

1. **Фаза 1** — открывает **Telegram Web** в Chromium (Playwright), заходит **по номеру телефона** так же, как в браузере. Для **каждого** аккаунта в консоли нужно ввести **код из Telegram**. Сохраняется профиль браузера в `output/mytg_web_storage/` (это **не** файл `.session` Telethon).
2. **Пауза** (по умолчанию сутки, ключ `mytg_wait_after_web_sec` в `config/settings.json`) — можно прервать и запустить фазу 2 позже.
3. **Фаза 2** — **my.telegram.org**, код подтверждения из чата в Web, создание приложения, запись **api_id** / **api_hash** в `config/accounts.json` и в `<stem>.json` рядом с `.session`.

Файлы **`*.session` Telethon** на диске **не подставляются** в браузер на фазе 1: номер берётся **только из имени файла**, чтобы не дублировать телефон в JSON. После фазы 2 ключи дописываются к тому же **stem**, что у `.session`.

## Подготовка

1. **Прокси** — залить на сервер как **`config/proxies.txt`** (в корне проекта рядом с `main.py` это папка `config/`). В `settings.json` по умолчанию пул читается оттуда (см. ключ `proxies` → `files`).
2. **Сессии из архива** — ZIP с парами **`<stem>.json` + `<stem>.session`** (как в вашем `sessions.zip`; если переименуете в **`accounts.zip`**, удобно импортировать через меню **9 → 1** «Импорт ZIP» — файлы скопируются в каталог **`telethon_session_dir`** из `settings.json`, обычно `sessions/`). Можно вместо меню распаковать архив вручную в этот каталог.
3. **Имена файлов** — **8–15 цифр** номера в международном формате; допускается ведущий **`+`** (пример: **`+123456789012.session`** — так и оставляйте). Без плюса тоже можно: **`375291234567.session`**. Суффикс после **`_`** (коллизия) отбрасывается: **`+375…_a1b2.session`** → номер **`+375…`**.
4. Зависимости: `pip install -r requirements.txt`, затем **`playwright install chromium`**.

### Linux: `libatk-1.0.so.0` / другие `.so` не найдены

На «голом» сервере или в Docker одного `playwright install chromium` мало — нужны **системные** библиотеки для Chromium. Иначе в логе будет что-то вроде: `error while loading shared libraries: libatk-1.0.so.0`.

**Ubuntu / Debian (предпочтительно):**

```bash
sudo playwright install-deps
# если `playwright` не в PATH:
python -m playwright install-deps
```

**Минимально** (часто хватает для первой ошибки ATK):

```bash
sudo apt-get update
sudo apt-get install -y libatk1.0-0 libatk-bridge2.0-0
```

Дальше снова `playwright install chromium` и запуск с `xvfb-run` (см. ниже). На RHEL/AlmaLinux смотрите вывод `playwright install-deps` или [документацию Playwright](https://playwright.dev/docs/intro#system-requirements).

## Сервер без монитора (Linux)

Если **`"mytg_headless": false`**, но переменных **`DISPLAY` / `WAYLAND_DISPLAY`** нет, запуск **автоматически идёт в headless** (иначе Chromium падает с «Missing X server»). Чтобы оставить не-headless, используйте **`xvfb-run`** ниже.

В `config/settings.json`: **`"mytg_headless": false`**, запуск:

```bash
xvfb-run -a python main.py --mytg-phase1 --mytg-from-sessions
```

Дальше фаза 2 (после паузы или сразу, если `mytg_wait_after_web_sec` = 0):

```bash
xvfb-run -a python main.py --mytg-phase2 --mytg-from-sessions
```

Полный цикл с вопросом про ожидание в консоли:

```bash
xvfb-run -a python main.py --mytg-full --mytg-from-sessions
```

Состояние: `output/mytegram_portal_state.json` (при необходимости `--mytg-state путь`).

## Меню

**Главное меню → 9 → 4** — пункты **4–6** = тот же режим «**телефон из имени .session** + прокси из config».

## По очереди и коды

Аккаунты обрабатываются **последовательно**: для каждого номера откроется браузер, в консоли появится запрос **«Код входа Telegram для +…»** — вводите код из приложения/SMS. Затем следующий файл `.session` в списке (сортировка по имени) и следующий прокси из пула (round-robin).

## Проверка

- После фазы 2 в `accounts.json` у соответствующего `session_name` должны появиться **api_id** и **api_hash**.
- Для рассылки через Telethon позже может понадобиться отдельный вход Telethon с этими ключами (`.session` Telethon и Web — разные механизмы).
