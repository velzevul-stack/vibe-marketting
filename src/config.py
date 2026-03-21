"""Конфигурация приложения."""
import json
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from src.cli_input import digits_only


def _config_dir() -> Path:
    return Path(__file__).parent.parent / "config"


def settings_json_path() -> Path:
    """Путь к config/settings.json."""
    return _config_dir() / "settings.json"


def _load_settings() -> dict:
    """Загрузить settings.json."""
    path = _config_dir() / "settings.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in (data or {}).items() if not k.startswith("_")}


def is_proxy_enabled() -> bool:
    """Использовать ли прокси в рантайме (поиск, клиенты Telethon)."""
    return bool(_load_settings().get("proxy_enabled", True))


def set_proxy_enabled(enabled: bool) -> tuple[bool, str]:
    """
    Записать ``proxy_enabled`` в config/settings.json.
    Сохраняет остальные ключи файла; при отсутствии файла создаёт минимальный JSON.
    """
    path = settings_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8-sig").strip()
            data = json.loads(raw) if raw else {}
        except (OSError, json.JSONDecodeError) as e:
            return False, f"Не удалось прочитать settings.json: {e}"
        if not isinstance(data, dict):
            return False, "settings.json: корень должен быть объектом JSON"
    else:
        data = {}
    data["proxy_enabled"] = bool(enabled)
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        return False, f"Не удалось записать settings.json: {e}"
    return True, str(path)


class Settings:
    """Настройки из config/settings.json."""

    def __init__(self, data: dict | None = None):
        self._data = data or _load_settings()
        delays = self._data.get("delays", {})
        self.delay_join_min: int = delays.get("join_min", 30)
        self.delay_join_max: int = delays.get("join_max", 120)
        self.delay_contact_min: int = delays.get("contact_min", 60)
        self.delay_contact_max: int = delays.get("contact_max", 180)
        self.delay_invite_min: int = delays.get("invite_min", 90)
        self.delay_invite_max: int = delays.get("invite_max", 240)
        self.delay_search_min: float = delays.get("search_min", 2.0)
        self.delay_search_max: float = delays.get("search_max", 6.0)
        self.delay_scrape_between_groups: float = float(
            delays.get("scrape_between_groups", 2.0)
        )
        self.delay_scrape_per_message: float = float(
            delays.get("scrape_per_message", 0.0)
        )
        self.telegram_index_api_key: str | None = self._data.get("telegram_index_api_key") or None
        self.ddgs_search_enabled: bool = self._data.get("ddgs_search_enabled", True)
        self.tg_catalog_enabled: bool = self._data.get("tg_catalog_enabled", True)
        # Не строить запросы по городам из blocklist и отбрасывать такие группы в выдаче (см. data/russian_cities_blocklist.json)
        self.exclude_russian_cities_in_search: bool = bool(
            self._data.get("exclude_russian_cities_in_search", True)
        )
        # Файл со списком ссылок на группы (в config/), по одной t.me на строку — для п.2/3 без поиска
        _glf = self._data.get("group_links_file")
        self.group_links_file: str = (
            str(_glf).strip() if _glf and str(_glf).strip() else "group_links.txt"
        )
        self.tgstat_token: str | None = self._data.get("tgstat_token") or None
        self.telemetr_api_key: str | None = self._data.get("telemetr_api_key") or None
        # Папка с *.session (относительно корня проекта / cwd), например "accounts" или "sessions"
        self.telethon_session_dir: str = self._data.get("telethon_session_dir", "sessions")
        # Для массовой подготовки аккаунтов (п. b): пароль облачного 2FA; лучше задать в settings.json локально
        self.bulk_2fa_password: str | None = (self._data.get("bulk_2fa_password") or None)
        self.bulk_prepare_delay_sec: float = float(
            self._data.get("bulk_prepare_delay_sec", 5.0)
        )
        # True: при каждом запуске — подтянуть *.session + рядом *.json → accounts.json
        self.sync_sessions_on_startup: bool = bool(
            self._data.get("sync_sessions_on_startup", True)
        )
        # True: при каждом запуске меню сначала round-robin прокси из пула → accounts.json
        self.assign_proxies_on_startup: bool = bool(
            self._data.get("assign_proxies_on_startup", False)
        )
        # False — не использовать прокси ни из пула, ни из accounts.json (поиск, Telethon)
        self.proxy_enabled: bool = bool(self._data.get("proxy_enabled", True))
        # False — сбор базы (Telethon) без прокси из пула; join/invite по-прежнему с пулом/аккаунтом
        self.scrape_use_proxy: bool = bool(self._data.get("scrape_use_proxy", True))
        # Имя session_name из accounts.json — только эта сессия для сбора (остальные аккаунты не ротируются)
        _ssn = self._data.get("scrape_session_name")
        self.scrape_session_name: str | None = (
            str(_ssn).strip() if _ssn and str(_ssn).strip() else None
        )
        # Дефолтные api для автопривязки .session → accounts.json (меню сессий, п.4)
        tda = self._data.get("telethon_default_api") or {}
        self.default_telethon_api_id: int | None = None
        try:
            if tda.get("api_id") is not None and str(tda.get("api_id", "")).strip() != "":
                self.default_telethon_api_id = int(tda["api_id"])
        except (TypeError, ValueError):
            pass
        _h = tda.get("api_hash")
        self.default_telethon_api_hash: str | None = (
            str(_h).strip() if _h and str(_h).strip() else None
        )


def clone_settings(**overrides) -> Settings:
    """Копия настроек из settings.json с подменой верхнеуровневых ключей (один прогон меню)."""
    import copy

    data = copy.deepcopy(_load_settings())
    for k, v in overrides.items():
        data[k] = v
    return Settings(data=data)


# Если bulk_2fa_password в settings пустой — автоподстановка при 2FA в консоли
AUTO_2FA_PASSWORD_DEFAULT = "suka228"


def effective_2fa_password(settings: Settings | None = None) -> str:
    """Пароль 2FA: из settings, иначе AUTO_2FA_PASSWORD_DEFAULT."""
    s = settings if settings is not None else Settings()
    p = (s.bulk_2fa_password or "").strip()
    return p if p else AUTO_2FA_PASSWORD_DEFAULT


def telethon_session_dir_path(settings: Settings | None = None) -> Path:
    """Каталог для Telethon *.session (создаётся при необходимости)."""
    s = settings or Settings()
    p = Path(s.telethon_session_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def telethon_session_file(session_name: str, settings: Settings | None = None) -> Path:
    """Путь к файлу сессии: <telethon_session_dir>/<name>.session"""
    return telethon_session_dir_path(settings) / f"{session_name}.session"


def assign_proxies_round_robin_to_accounts(
    settings: Settings | None = None,
) -> tuple[bool, str]:
    """
    Назначить прокси из пула аккаунтам (round-robin).
    Сохраняет весь accounts.json (включая служебные строки), не только список аккаунтов.
    Дополнительно: если есть ``sessions/<имя>.json``, в него пишется то же поле ``proxy``.
    """
    s = settings or Settings()
    proxies = load_proxy_pool_from_config()
    if not proxies:
        return False, "Нет прокси в пуле (proxies.txt / settings.json)"
    all_rows = load_accounts_all()
    tele = load_accounts()
    if not tele:
        return False, "Нет аккаунтов в accounts.json"
    for i, acc in enumerate(tele):
        p = proxies[i % len(proxies)]
        acc["proxy"] = p
        name = acc.get("session_name")
        if name:
            write_proxy_to_session_sidecar(str(name), p, s)
    save_accounts_all(all_rows)
    return True, str(accounts_json_path())


def load_json(path: Path) -> dict | list:
    """Загрузить JSON-файл."""
    if not path.exists():
        return {} if "exclude" not in path.name else {"generic_fleamarket": [], "vape_markers_required": True}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_keywords() -> dict:
    """Загрузить ключевые слова."""
    path = Path(__file__).parent.parent / "config" / "keywords.json"
    return load_json(path)


def load_exclude_keywords() -> dict:
    """Загрузить стоп-слова для исключения барахолок."""
    path = Path(__file__).parent.parent / "config" / "exclude_keywords.json"
    return load_json(path)


def load_cities() -> list[str]:
    """Загрузить список городов и населённых пунктов Беларуси, отсортированный по населению (крупные первыми)."""
    path = Path(__file__).parent.parent / "data" / "cities_by.json"
    data = load_json(path)
    if not isinstance(data, list):
        return []
    # Поддержка формата [{"name": "...", "pop": N}, ...] — сортировка по pop
    if data and isinstance(data[0], dict):
        sorted_data = sorted(data, key=lambda x: x.get("pop", 0), reverse=True)
        return [item.get("name", "") for item in sorted_data if item.get("name")]
    # Обратная совместимость: [str, str, ...]
    return [str(x) for x in data if x]


def load_russian_cities_blocklist_raw() -> list[str]:
    """Строки из data/russian_cities_blocklist.json (как заданы в файле)."""
    path = Path(__file__).parent.parent / "data" / "russian_cities_blocklist.json"
    if not path.exists():
        return []
    data = load_json(path)
    if isinstance(data, dict) and isinstance(data.get("cities"), list):
        items = data["cities"]
    elif isinstance(data, list):
        items = data
    else:
        return []
    return [str(x).strip() for x in items if str(x).strip()]


def russian_cities_blocklist_effective() -> frozenset[str]:
    """
    Множество нижнего регистра для фильтрации. Имена, совпадающие с городами из cities_by.json,
    убираются — чтобы не резать одноимённые белорусские населённые пункты (Иваново, Дзержинск и т.д.).
    """
    raw_lower = {x.lower() for x in load_russian_cities_blocklist_raw()}
    by_exact = {(c or "").strip().lower() for c in load_cities() if (c or "").strip()}
    return frozenset(x for x in raw_lower if x not in by_exact)


def load_manual_groups() -> list[str]:
    """Загрузить ручной список групп из groups.txt."""
    path = Path(__file__).parent.parent / "config" / "groups.txt"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    result = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            if "t.me" in line or "telegram" in line.lower():
                result.append(line)
    return result


def group_links_file_path(settings: Settings | None = None) -> Path:
    """
    Путь к txt со ссылками на группы (настройка ``group_links_file`` в settings.json).
    Если в имени есть ``/`` или ``\\`` — путь относительно cwd или абсолютный.
    """
    s = settings or Settings()
    name = (s.group_links_file or "group_links.txt").strip() or "group_links.txt"
    if "/" in name or "\\" in name:
        return Path(name).expanduser()
    return _config_dir() / name


def _normalize_telegram_group_link(raw: str) -> str | None:
    """Первая колонка строки — ссылка https://t.me/... или t.me/..."""
    line = (raw or "").strip()
    if not line or line.startswith("#"):
        return None
    link = line.split()[0].strip().strip('"').strip("'")
    low = link.lower()
    if "t.me/" not in low and "telegram.me/" not in low:
        return None
    if not link.startswith("http"):
        link = "https://" + link.lstrip("/")
    return link


def load_groups_from_links_txt(
    path: Path | None = None,
    settings: Settings | None = None,
) -> list[dict]:
    """
    Список групп в формате как у found_groups.json: по одной ссылке t.me / telegram.me на строку.
    """
    p = path if path is not None else group_links_file_path(settings)
    if not p.is_file():
        return []
    out: list[dict] = []
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        link = _normalize_telegram_group_link(line)
        if not link:
            continue
        n += 1
        m = re.search(r"(?:t\.me|telegram\.me)/(.+)", link, re.I)
        tail = (m.group(1).strip("/") if m else str(n))[:80]
        title = tail or f"group_{n}"
        out.append(
            {
                "id": tail,
                "title": title,
                "link": link,
                "members": 0,
                "description": "",
                "source": "group_links_txt",
            }
        )
    return out


def normalize_proxy_line(line: str) -> str:
    """
    Привести строку прокси к URL для httpx/Telethon.

    Поддержка:
    - Уже URL: ``http://...``, ``socks5://...`` — без изменений
    - ``host:port:user:pass`` — как в proxies.txt у многих провайдеров
    - ``host:port`` — без авторизации → ``http://host:port``
    """
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return ""
    if "://" in line:
        return line
    parts = line.split(":", 3)
    if len(parts) == 4 and parts[1].isdigit():
        host, port, user, password = parts
        u, p = quote(user, safe=""), quote(password, safe="")
        return f"http://{u}:{p}@{host}:{port}"
    if len(parts) == 2 and parts[1].isdigit():
        return f"http://{parts[0]}:{parts[1]}"
    return line


def is_placeholder_proxy_url(proxy: str | None) -> bool:
    """
    Шаблонные URL из документации (example.com и т.п.).
    Их часто копируют в accounts.json; проверка прокси при этом смотрит на proxies.txt — источники разные.
    """
    if not proxy or not str(proxy).strip():
        return False
    low = str(proxy).lower()
    return "example.com" in low or "example.org" in low


def proxy_url_to_telethon(
    proxy: str | tuple | list | dict | None,
) -> tuple | dict | None:
    """
    Telethon ожидает ``proxy`` как tuple / list / dict (PySocks / python_socks), не строку URL.
    Строки ``http(s)://``, ``socks4://``, ``socks5://`` (как в accounts.json после назначения)
    преобразуются в кортеж ``(type, host, port[, rdns, user, password])``.
    """
    if proxy is None:
        return None
    if isinstance(proxy, dict):
        return proxy
    if isinstance(proxy, tuple):
        return proxy
    if isinstance(proxy, list):
        return tuple(proxy)

    raw = normalize_proxy_line(str(proxy).strip())
    if not raw:
        return None
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname
    if not host:
        return None

    port = parsed.port
    if scheme in ("http", "https"):
        ptype = "http"
        if port is None:
            port = 443 if scheme == "https" else 80
    elif scheme == "socks5":
        ptype = "socks5"
        if port is None:
            port = 1080
    elif scheme == "socks4":
        ptype = "socks4"
        if port is None:
            port = 1080
    else:
        ptype = "http"
        if port is None:
            port = 8080

    user = parsed.username
    password = parsed.password
    if user:
        user = unquote(user)
    if password:
        password = unquote(password)

    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return None

    if user or password:
        return (ptype, host, port_i, True, user or None, password or None)
    return (ptype, host, port_i)


def write_proxy_to_session_sidecar(
    session_name: str, proxy_url: str, settings: Settings | None = None
) -> None:
    """
    Если рядом с сессией есть ``<session_name>.json``, дописать/обновить поле ``proxy`` (URL строкой).
    Сам .session Telethon прокси не хранит — только для единого места рядом с api.
    """
    if not (session_name or "").strip() or not (proxy_url or "").strip():
        return
    s = settings or Settings()
    path = telethon_session_dir_path(s) / f"{session_name.strip()}.json"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
        data: dict = json.loads(text) if text else {}
        if not isinstance(data, dict):
            return
        data["proxy"] = str(proxy_url).strip()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass


def _read_proxy_file(filepath: Path) -> list[str]:
    """Прочитать прокси из файла."""
    if not filepath.exists():
        return []
    out: list[str] = []
    for l in filepath.read_text(encoding="utf-8").splitlines():
        raw = l.strip()
        if not raw or raw.startswith("#"):
            continue
        norm = normalize_proxy_line(raw)
        if norm:
            out.append(norm)
    return out


def load_proxy_pool_from_config() -> list[str]:
    """
    Список прокси из settings.json (files / list) и proxies.txt.
    Не учитывает ``proxy_enabled`` — для назначения аккаунтам и проверки пула.
    """
    config_dir = _config_dir()
    settings = _load_settings()
    proxies_cfg = settings.get("proxies", {})

    source = proxies_cfg.get("source", "files")
    files = proxies_cfg.get("files", ["proxies.txt"])
    proxy_list = proxies_cfg.get("list", [])

    result: list[str] = []

    if source in ("files", "both"):
        for fname in files:
            if isinstance(fname, str):
                path = config_dir / fname
                result.extend(_read_proxy_file(path))

    if source in ("list", "both"):
        for p in proxy_list:
            if isinstance(p, str) and p.strip():
                n = normalize_proxy_line(p)
                if n:
                    result.append(n)

    if not result:
        path = config_dir / "proxies.txt"
        result = _read_proxy_file(path)

    return result


def load_proxies() -> list[str]:
    """Прокси для рантайма: пустой список, если в settings ``proxy_enabled: false``."""
    if not is_proxy_enabled():
        return []
    return load_proxy_pool_from_config()


def mask_proxy_display(proxy: str | None) -> str:
    """Безопасное отображение прокси: host:***:port."""
    if not proxy:
        return "—"
    import re
    m = re.search(r"@([^:/]+):(\d+)", proxy)
    if m:
        host, port = m.group(1), m.group(2)
        if len(host) > 8:
            host = host[:4] + "***" + host[-2:]
        return f"{host}:{port}"
    m = re.search(r"://([^:/]+):(\d+)", proxy)
    if m:
        host, port = m.group(1), m.group(2)
        if len(host) > 8:
            host = host[:4] + "***" + host[-2:]
        return f"{host}:{port}"
    return "***"


class ProxyPool:
    """Пул прокси для поиска и сбора базы. Round-robin."""

    def __init__(self):
        self._proxies = load_proxies()
        self._index = 0

    def get_next(self) -> str | None:
        """Следующий прокси из пула."""
        if not self._proxies:
            return None
        p = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        return p

    def get_next_with_info(self) -> tuple[str | None, str]:
        """(proxy, display_str). display_str = 'Прокси 3/7: host:port'."""
        p = self.get_next()
        n = len(self._proxies)
        idx = (self._index - 1) % n + 1 if n else 0
        disp = mask_proxy_display(p)
        info = f"{idx}/{n} ({disp})" if n else "—"
        return p, info

    @property
    def proxies(self) -> list[str]:
        return self._proxies.copy()


def accounts_json_path() -> Path:
    """Путь к config/accounts.json."""
    return _config_dir() / "accounts.json"


def load_accounts_all() -> list[dict]:
    """Все объекты из accounts.json (включая комментарии-заглушки)."""
    data = load_json(accounts_json_path())
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def save_accounts_all(rows: list[dict]) -> None:
    """Сохранить accounts.json целиком."""
    path = accounts_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_accounts() -> list[dict]:
    """Аккаунты Telethon: api_id, api_hash, без шаблонов _template."""
    return [
        a
        for a in load_accounts_all()
        if a.get("api_id")
        and a.get("api_hash")
        and not a.get("_template")
    ]


def is_telethon_account_row(row: dict) -> bool:
    """Строка в accounts.json — полноценный TG-аккаунт (не шаблон)."""
    return bool(
        row.get("api_id")
        and row.get("api_hash")
        and not row.get("_template")
    )


def upsert_telethon_account(
    session_name: str,
    api_id: int,
    api_hash: str,
    *,
    phone: str | None = None,
    proxy: str | None = None,
) -> None:
    """Добавить или заменить аккаунт по session_name (сохраняет прочие строки JSON)."""
    name = (session_name or "").strip()
    if not name:
        raise ValueError("session_name пустой")
    rows = load_accounts_all()
    rows = [
        r
        for r in rows
        if not (is_telethon_account_row(r) and r.get("session_name") == name)
    ]
    entry: dict = {
        "session_name": name,
        "api_id": int(api_id),
        "api_hash": str(api_hash).strip(),
    }
    if phone and str(phone).strip():
        entry["phone"] = str(phone).strip()
    if proxy and str(proxy).strip():
        entry["proxy"] = str(proxy).strip()
    rows.append(entry)
    save_accounts_all(rows)


def session_bind_file_path() -> Path:
    """Список сессий для привязки (как proxies.txt)."""
    return _config_dir() / "session_bind.txt"


def parse_session_bind_line(line: str) -> dict | None:
    """
    Одна строка session_bind.txt:

    - только session_name — api возьмутся из telethon_default_api в settings
    - session_name:api_id:api_hash
    - session_name:api_id:api_hash:phone
    """
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None
    if ":" not in line:
        return {
            "session_name": line,
            "api_id": None,
            "api_hash": None,
            "phone": None,
        }
    parts = line.split(":", 3)
    if len(parts) < 3:
        return None
    name, aid_s, ahash = parts[0].strip(), parts[1].strip(), parts[2].strip()
    phone = parts[3].strip() if len(parts) > 3 else None
    aid_digits = digits_only(aid_s)
    if not aid_digits:
        return None
    try:
        api_id = int(aid_digits)
    except ValueError:
        return None
    if not name or not ahash:
        return None
    return {
        "session_name": name,
        "api_id": api_id,
        "api_hash": ahash,
        "phone": phone or None,
    }


def load_session_bind_specs_from_file() -> list[dict]:
    """Разобрать config/session_bind.txt."""
    path = session_bind_file_path()
    if not path.exists():
        return []
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        spec = parse_session_bind_line(raw)
        if spec:
            out.append(spec)
    return out
