# Пакет campaign: структура каталога, API и прокси

Один каталог (например `./campaign`) содержит всё для импорта сессий и рассылки из меню (**9 → 6**) или из CLI: `--broadcast`, `--csv-broadcast`.

## Обязательные файлы

| Файл | Назначение |
|------|------------|
| `accounts.zip` | Пары `stem.json` + `stem.session` для Telethon |
| `apis.txt` | Строки `api_id:api_hash` — **пул для round-robin** по аккаунтам, которым не заданы ключи явно |
| `proxy.txt` **или** `proxies.txt` | По одному прокси на строку — **пул для round-robin** (если есть непустой `proxy.txt`, он **предпочитается** старому имени `proxies.txt`) |
| `text_1.txt`, `text_2.txt` | Тексты рассылки (чередуются по хешу получателя) |
| `1.jpg` … `3.jpg` | Медиа (если рассылка с фото; для текста только — флаг `--broadcast-text-only` / `--csv-broadcast-text-only`) |

Форматы прокси в `proxy.txt`: как в `config/proxies.txt` — готовый URL (`socks5://…`, `http://…`) или `host:port:user:pass`.

## Дополнительные ZIP (мультипакет)

- `accounts2.zip`, `accounts3.zip`, …
- Рядом: `apis2.txt`, `apis3.txt`, … и **`proxy2.txt`** (или `proxies2.txt`), и т.д.

Сессии из `accounts2.zip` получают RR API из `apis2.txt` и RR прокси из `proxy2.txt` / `proxies2.txt`, если для них нет явной привязки в файлах ниже.

## Явная привязка API к сессиям — `apis_sessions.txt`

Каждая непустая строка (кроме `#`):

```text
api_id:api_hash stem1 stem2 stem3
```

Стем — имя файла сессии **без** `.session`. Одна и та же сессия не должна встречаться в двух строках.

Эти аккаунты получают **указанную** пару `api_id`/`api_hash` (приоритет над RR из `apis.txt`).

## Явная привязка API + прокси к одной сессии — `sessions_bind.txt`

Одна строка — один аккаунт. **Ровно три поля**, разделённые пробелами:

```text
api_id:api_hash <прокси_одной_строкой> <stem>
```

Примеры:

```text
12345678:0123456789abcdef0123456789ab socks5://user:pass@203.0.113.1:1080 acc01
87654321:fedcba0987654321fedcba098765 10.0.0.5:8000:login:password acc02
```

- Во втором поле **не должно быть пробелов** (URL или `host:port:user:pass`).
- Указанная сессия получает **и** эти ключи приложения, **и** этот прокси. Это **высший приоритет** для обоих полей.

Если сессия перечислена в `sessions_bind.txt`, строки для неё в `apis_sessions.txt` **игнорируются** (ключи уже заданы bind-файлом).

## Порядок назначения (после импорта ZIP)

1. **`sessions_bind.txt`** — полная привязка api + proxy + stem.
2. **`apis_sessions.txt`** — только API для перечисленных стемов (стемы из п.1 пропускаются).
3. **Round-robin `apis.txt`** — для импортированных стемов, у которых ещё нет `api_id`/`api_hash` (отдельно по каждому слайсу: `apis.txt`, `apis2.txt`, …).
4. **Round-robin `proxy.txt` / `proxies.txt`** — для импортированных стемов без непустого прокси (отдельно по слайсу: `proxy.txt` или `proxies.txt`, `proxy2.txt` или `proxies2.txt`, …).

Аккаунты с историей успешной рассылки в SQLite по-прежнему **не перезаписываются** при назначении прокси из пула (см. `assign_proxies_round_robin_to_accounts`).

## CLI

Рассылка из БД:

```bash
python main.py --broadcast ./campaign --broadcast-limit 500
```

Рассылка по CSV (без БД, см. также [README](../README.md)):

```bash
python main.py --csv-broadcast ./campaign --csv-recipients ./members.csv --csv-delay-minutes 30
```

Конфликт имён при распаковке ZIP: `--broadcast-zip-conflict skip|overwrite`.

## Если новые сессии из ZIP не импортировались

При **нуле** импортированных стемов CLI `--broadcast` пытается один раз назначить API и прокси **всем** аккаунтам из `accounts` слайса (как раньше). В меню без импорта показывается предупреждение; при необходимости используйте назначение вручную или повторите импорт.

## Конфиг `config/proxies.txt`

Файлы **`proxy.txt` в каталоге пакета** и **`config/proxies.txt`** — разные вещи: первый относится только к campaign-пакету, второй — к общему пулу приложения (поиск, меню **9 → 2** и т.д.).
