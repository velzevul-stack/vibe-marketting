# Настройка конфигурации

Подробная инструкция по прокси и аккаунтам: [docs/PROXY_AND_ACCOUNTS.md](../docs/PROXY_AND_ACCOUNTS.md)

## Прокси: поиск → сбор базы → аккаунты

1. **Поиск групп** и **сбор базы** — используют прокси из пула (proxies.txt / settings.json)
2. **Аккаунты** — при join/add/invite берут proxy из accounts.json или из пула
3. **Назначить прокси аккаунтам** (п.7 меню) — перераспределяет прокси из пула по аккаунтам в accounts.json

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
| `phone` | Номер телефона в формате +375291234567 |
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
