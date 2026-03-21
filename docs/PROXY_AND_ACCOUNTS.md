# Настройка прокси и аккаунтов

## Поиск групп: источники

| Источник | API-ключ | Описание |
|----------|----------|----------|
| **RapidAPI (Telegram Index)** | `telegram_index_api_key` | Платный, поиск по городам и темам |
| **TGStat** | `tgstat_token` | Платный, 2.9M+ каналов (api.tgstat.ru) |
| **Telemetr** | `telemetr_api_key` | Free: 1000 req/мес, 1.8M+ каналов (api.telemetr.io) |
| **TG Catalog (tg-cat.com)** | Не нужен | Бесплатный каталог (1000+ вейп-чатов) |
| **DuckDuckGo (ddgs)** | Не нужен | Бесплатный веб-поиск site:t.me |
| **Ручной список** | — | config/groups.txt |

**Дедупликация:** все источники объединяются без дублей (по t.me/username).

В `settings.json`:
- `"tgstat_token": ""` — токен с tgstat.ru (API Stat S+)
- `"telemetr_api_key": ""` — ключ из @telemetrio_api_bot
- `"ddgs_search_enabled": false` — отключить DuckDuckGo
- `"tg_catalog_enabled": false` — отключить TG Catalog

## Прокси

### Форматы

- **SOCKS5:** `socks5://user:pass@host:port` или `socks5://host:port` (без авторизации)
- **HTTP:** `http://user:pass@host:port` или `http://host:port`

### Где используются

| Функция | Источник прокси |
|---------|-----------------|
| Поиск групп | Пул (round-robin) |
| Сбор базы | Пул (round-robin), параллельно |
| Вступление в группы, контакты, приглашения | Из `accounts.json` или пул |
| Назначить прокси (меню **8 → 1**) | Перераспределяет пул по аккаунтам |

### Вариант A — из файлов

Создайте `config/proxies.txt`, по одному прокси на строку:

```
socks5://user:pass@proxy1.example.com:1080
socks5://user:pass@proxy2.example.com:1080
http://user:pass@proxy3.example.com:8080
```

**Короткий формат** (одна строка = один прокси, превращается в `http://`):

```
188.119.126.107:9274:логин:пароль
```

Также поддерживается `IP:порт` без логина. Для SOCKS5 по-прежнему полный URL: `socks5://...`.

В `config/settings.json`:

```json
"proxies": {
  "source": "files",
  "files": ["proxies.txt"],
  "list": []
}
```

Файлы ищутся в папке `config/`. Можно указать несколько: `["proxies.txt", "proxies2.txt"]`.

### Вариант B — список в settings.json

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

### Вариант C — файлы + список

```json
"proxies": {
  "source": "both",
  "files": ["proxies.txt"],
  "list": ["socks5://extra:proxy@host:1080"]
}
```

---

## Аккаунты

### Получение api_id и api_hash

1. Перейдите на [my.telegram.org](https://my.telegram.org)
2. Войдите по номеру телефона
3. Создайте приложение (если ещё нет)
4. Скопируйте `api_id` и `api_hash`

### Формат accounts.json

| Поле | Описание |
|------|----------|
| `session_name` | Уникальное имя сессии (например `account1`) |
| `api_id` | Число из my.telegram.org |
| `api_hash` | Строка из my.telegram.org |
| `phone` | Международный номер с `+` и кодом страны (любая страна, не только +375) |
| `proxy` | Прокси для аккаунта (опционально) |

### Пример

```json
[
  {
    "session_name": "account1",
    "api_id": 12345678,
    "api_hash": "abcdef1234567890abcdef1234567890",
    "phone": "+375291234567",
    "proxy": "socks5://user:pass@proxy.com:1080"
  }
]
```

### Сессии Telethon (.session)

Папка задаётся в `settings.json`: **`telethon_session_dir`** (по умолчанию `sessions`, в примере — `accounts`).  
Файл: `<telethon_session_dir>/<session_name>.session` (SQLite).

**Важно:** `api_id` и `api_hash` в `accounts.json` должны быть **от того же приложения**, которым создавалась сессия.

**Пункт меню `[a] Сессии Telethon`:**
- **Список** — `.session` в каталоге из `telethon_session_dir` и строки в `accounts.json`
- **Привязать готовый .session** — копирует в этот каталог + запись в `accounts.json`
- **Новая авторизация по телефону** — создаёт `.session` там же

**Пункт меню `[b] Подготовка аккаунтов`** (осторожно: меняет безопасность и сессии):
1. Если у аккаунта **ещё нет** облачного пароля 2FA — включает его (**без email**, пустой hint). Пароль: **`bulk_2fa_password`** в `settings.json`, а если пусто — встроенный дефолт из `src/config.py` (`AUTO_2FA_PASSWORD_DEFAULT`, без запроса в консоли). В меню **[a]** при входе по телефону пароль 2FA подставляется так же.
2. Назначает прокси из пула всем аккаунтам (как п.7), **не затирая** прочие строки в `accounts.json`.
3. Подключается **с прокси** и вызывает **`auth.resetAuthorizations`** — с **всех других устройств** выход, остаётся только текущая сессия.

Пауза между аккаунтами: **`bulk_prepare_delay_sec`** (сек). Папка `accounts/` в `.gitignore`.

Без меню можно вручную: положить `.session` в выбранный каталог, имя без `.session` = `session_name` в JSON.

### Назначить прокси аккаунтам (меню 8 → 1)

Если прокси заданы в пуле (proxies.txt или settings.json), главное меню **8**, затем **1**:
- Распределяет прокси по аккаунтам (round-robin)
- Обновляет `config/accounts.json`, добавляя поле `proxy` каждому аккаунту

Запустите после настройки пула прокси и перед join/add/invite.

---

## groups.txt

Ручной список групп для поиска и сбора базы.

Формат: одна ссылка на строку. Строки с `#` игнорируются.

```
https://t.me/vape_minsk
https://t.me/vape_grodno
# https://t.me/joinchat/xxxxx  — приватная группа по invite
```

Файл: `config/groups.txt` (создайте вручную, скопируйте `groups.txt.example`).
