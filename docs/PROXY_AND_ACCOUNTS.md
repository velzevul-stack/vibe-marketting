# Настройка прокси и аккаунтов

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
| Назначить прокси аккаунтам (п.7) | Перераспределяет пул по аккаунтам |

### Вариант A — из файлов

Создайте `config/proxies.txt`, по одному прокси на строку:

```
socks5://user:pass@proxy1.example.com:1080
socks5://user:pass@proxy2.example.com:1080
http://user:pass@proxy3.example.com:8080
```

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
| `phone` | Номер в формате +375291234567 |
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

### Первый вход

При первом запуске для каждого аккаунта:
1. В консоль придёт запрос кода
2. Код придёт в Telegram
3. Введите код
4. При включённой 2FA — введите пароль

Сессии сохраняются в `sessions/`. Повторная авторизация не нужна.

### Назначить прокси аккаунтам (п.7 меню)

Если прокси заданы в пуле (proxies.txt или settings.json), пункт 7 меню:
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
