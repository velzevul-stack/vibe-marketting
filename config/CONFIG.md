# Настройка конфигурации

Подробная инструкция по прокси и аккаунтам: [docs/PROXY_AND_ACCOUNTS.md](../docs/PROXY_AND_ACCOUNTS.md)

## Прокси: поиск → сбор базы → аккаунты

1. **Поиск групп** и **сбор базы** — используют прокси из пула (proxies.txt / settings.json)
2. **Аккаунты** — при join/add/invite берут proxy из accounts.json или из пула
3. **Назначить прокси аккаунтам** (главное меню **9** → **2** → **2**) — перераспределяет прокси из пула по аккаунтам в accounts.json

### После обновления / смены proxies.txt

- Разово из корня проекта: `python main.py --assign-proxies` (меню не открывается).
- Или меню **9** → **2** → **2** (с подтверждением).
- Или в `settings.json`: `"assign_proxies_on_startup": true` — при **каждом** запуске меню прокси перезапишутся из пула; после настройки верните `false`.

### Временно без прокси (поиск и Telegram)

- Команда: `python main.py --proxy off` — в `settings.json` выставляется `"proxy_enabled": false` (прямые соединения; поля `proxy` в `accounts.json` не стираются).
- Включить снова: `python main.py --proxy on`.
- Текущее значение: `python main.py --proxy status`.

### Сбор базы: меню 5 → 1 (один аккаунт)

- **П.5 → 1 → общий** — выбор строки из `accounts.json`, для этого прогона сбор **без прокси** (ни пул, ни поле `proxy` у аккаунта).
- **П.5 → 1 → отдельный** — новая сессия: имя файла, api (можно из `telethon_default_api`), телефон, затем вопрос **с прокси или без**, код и при необходимости 2FA в консоли; после сбора можно записать аккаунт в `accounts.json`.
- **П.5 → 2** — прежний режим: настройки `scrape_use_proxy` / пул / закрепление `scrape_session_name` из `settings.json`.

### Сбор базы: только основной аккаунт, без прокси, с задержкой

При включённом глобальном `proxy_enabled` пул по умолчанию идёт и на сбор. Чтобы **сбор** шёл **напрямую** (как основной телефон), а прокси оставался для поиска/API:

```json
"scrape_use_proxy": false
```

У **основного** аккаунта в `accounts.json` лучше убрать поле `proxy` или не назначать ему прокси — иначе Telethon всё ещё пойдёт через него.

Чтобы не ротировать аккаунты при сборе, задайте имя сессии (как в `accounts.json` → `session_name`):

```json
"scrape_session_name": "main"
```

Тогда группы обрабатываются **по одной** (без параллели на одной `.session`).

Задержки в `delays`:

```json
"scrape_between_groups": 5,
"scrape_per_message": 0.3
```

`scrape_per_message` — пауза после каждого сообщения (0 = выкл.). `scrape_between_groups` — пауза после каждой группы в воркере сбора (раньше было жёстко 2 с).

---

## 1. settings.json

Скопируйте `settings.json.example` в `settings.json`:

```
cp config/settings.json.example config/settings.json
```

### Прокси

**Вариант A — из файлов:**
```json
"proxies": {
  "source": "files",
  "files": ["proxies.txt", "proxies2.txt"],
  "list": []
}
```
Файлы ищутся в папке `config/`. По одному прокси на строку.

**Вариант B — список в настройках:**
```json
"proxies": {
  "source": "list",
  "files": [],
  "list": [
    "socks5://user:pass@host:1080",
    "http://user:pass@host:8080"
  ]
}
```

**Вариант C — оба (файлы + список):**
```json
"proxies": {
  "source": "both",
  "files": ["proxies.txt"],
  "list": ["socks5://extra:proxy@host:1080"]
}
```

### Задержки (секунды)

- `join_min/max` — между вступлениями в группы
- `contact_min/max` — между добавлением в контакты
- `invite_min/max` — между приглашениями в канал
- `search_min/max` — между запросами поиска (умное ожидание, снижает риск бана)

---

## 2. accounts.json

Скопируйте `accounts.json.example` в `accounts.json`:

```
cp config/accounts.json.example config/accounts.json
```

### Формат аккаунта

| Поле | Описание |
|------|----------|
| `session_name` | Имя сессии (уникальное), например `account1` |
| `api_id` | Получить на https://my.telegram.org |
| `api_hash` | Получить на https://my.telegram.org |
| `phone` | Международный номер с `+` и кодом страны (например +375…, +95…) |
| `proxy` | Прокси для аккаунта (опционально): `socks5://user:pass@host:port` |

### Пример

```json
[
  {
    "session_name": "account1",
    "api_id": 12345678,
    "api_hash": "ваш_api_hash_из_my_telegram",
    "phone": "+375291234567",
    "proxy": "socks5://user:pass@proxy.com:1080"
  }
]
```

При первом запуске потребуется ввести код из Telegram для авторизации.

---

## 3. groups.txt

Добавьте ссылки на группы (по одной на строку):

```
https://t.me/vape_minsk
https://t.me/joinchat/xxxxx
```
