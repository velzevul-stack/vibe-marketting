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


def set_telethon_default_api(api_id: int, api_hash: str) -> tuple[bool, str]:
    """
    Записать telethon_default_api (api_id, api_hash) в config/settings.json.
    Сохраняет остальные ключи; корень — объект JSON.
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
    data["telethon_default_api"] = {
        "api_id": int(api_id),
        "api_hash": str(api_hash).strip(),
    }
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
        self.delay_broadcast_min: int = int(delays.get("broadcast_min", 45))
        self.delay_broadcast_max: int = int(delays.get("broadcast_max", 120))
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
        _cd = self._data.get("campaign_dir")
        self.campaign_dir: str = (
            str(_cd).strip() if _cd and str(_cd).strip() else "campaign"
        )
        # Рассылка: лимит исходящих ЛС на аккаунт за календарный день UTC (см. account_broadcast_daily в БД)
        self.broadcast_daily_limit_per_account: int = int(
            self._data.get("broadcast_daily_limit_per_account", 25)
        )
        self.broadcast_homoglyph_enabled: bool = bool(
            self._data.get("broadcast_homoglyph_enabled", True)
        )
        try:
            self.broadcast_homoglyph_probability: float = float(
                self._data.get("broadcast_homoglyph_probability", 0.12)
            )
        except (TypeError, ValueError):
            self.broadcast_homoglyph_probability = 0.12


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


def bundle_round_robin_account_rows(all_rows: list[dict]) -> list[dict]:
    """
    Записи с ``session_name`` (не шаблон) — для round-robin API и прокси,
    в том числе до назначения api_id/api_hash.
    """
    out: list[dict] = []
    for r in all_rows:
        if not isinstance(r, dict) or r.get("_template"):
            continue
        if (r.get("session_name") or "").strip():
            out.append(r)
    return out


def assign_proxies_round_robin_to_accounts(
    settings: Settings | None = None,
    proxy_pool: list[str] | None = None,
) -> tuple[bool, str]:
    """
    Назначить прокси из пула аккаунтам (round-robin).
    Если передан ``proxy_pool``, используется он; иначе пул из config (proxies.txt / settings).
    Сохраняет весь accounts.json (включая служебные строки), не только список аккаунтов.
    Дополнительно: если есть ``sessions/<имя>.json``, в него пишется то же поле ``proxy``.
    """
    s = settings or Settings()
    proxies = proxy_pool if proxy_pool is not None else load_proxy_pool_from_config()
    if not proxies:
        return False, "Нет прокси в пуле (proxies.txt / settings.json)"
    all_rows = load_accounts_all()
    targets = bundle_round_robin_account_rows(all_rows)
    if not targets:
        return False, "Нет аккаунтов (session_name) в accounts.json"
    for i, acc in enumerate(targets):
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
    - ``host:port:user:pass`` (часто в proxies.txt провайдеров) → ``http://user:pass@host:port``
    - ``host:port`` — без авторизации → ``http://host:port``
    """
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return ""
    if "://" in line:
        return line
    parts = line.split(":", 3)
    if len(parts) == 4:
        host, port_s, user, password = (p.strip() for p in parts)
        if not host or not port_s.isdigit():
            return ""
        try:
            port_i = int(port_s)
        except ValueError:
            return ""
        if not (1 <= port_i <= 65535):
            return ""
        u, pw = quote(user, safe=""), quote(password, safe="")
        return f"http://{u}:{pw}@{host}:{port_i}"
    if len(parts) == 2:
        host, port_s = parts[0].strip(), parts[1].strip()
        if not host or not port_s.isdigit():
            return line
        try:
            port_i = int(port_s)
        except ValueError:
            return line
        if not (1 <= port_i <= 65535):
            return line
        return f"http://{host}:{port_i}"
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


def write_api_to_session_sidecar(
    session_name: str,
    api_id: int,
    api_hash: str,
    settings: Settings | None = None,
) -> None:
    """Обновить api_id/api_hash в ``<session_name>.json`` рядом с .session (если файл есть)."""
    if not (session_name or "").strip():
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
        data["api_id"] = int(api_id)
        data["api_hash"] = str(api_hash).strip()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass


def remove_api_from_session_sidecar(
    session_name: str,
    settings: Settings | None = None,
) -> bool:
    """
    Удалить ``api_id`` / ``api_hash`` из sidecar (и зеркальные ``app_id`` / ``app_hash``, если есть).
    Возвращает True, если файл был изменён.
    """
    if not (session_name or "").strip():
        return False
    s = settings or Settings()
    path = telethon_session_dir_path(s) / f"{session_name.strip()}.json"
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
        data: dict = json.loads(text) if text else {}
        if not isinstance(data, dict):
            return False
        changed = False
        for k in (
            "api_id",
            "api_hash",
            "app_id",
            "app_hash",
            "apiId",
            "apiHash",
        ):
            if k in data:
                del data[k]
                changed = True
        for nest in ("telegram", "app", "telethon", "session", "credentials"):
            sub = data.get(nest)
            if isinstance(sub, dict):
                for k in ("api_id", "api_hash", "app_id", "app_hash"):
                    if k in sub:
                        del sub[k]
                        changed = True
        if changed:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return changed
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def remove_proxy_from_session_sidecar(
    session_name: str,
    settings: Settings | None = None,
) -> bool:
    """Удалить поле ``proxy`` из sidecar (верхний уровень и типичные вложенные блоки)."""
    if not (session_name or "").strip():
        return False
    s = settings or Settings()
    path = telethon_session_dir_path(s) / f"{session_name.strip()}.json"
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
        data: dict = json.loads(text) if text else {}
        if not isinstance(data, dict):
            return False
        changed = False
        if "proxy" in data:
            del data["proxy"]
            changed = True
        for nest in ("telegram", "app", "telethon", "session", "credentials"):
            sub = data.get(nest)
            if isinstance(sub, dict) and "proxy" in sub:
                del sub["proxy"]
                changed = True
        if changed:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return changed
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


_SIDECAR_SECRET_KEYS = frozenset(
    {
        "api_id",
        "api_hash",
        "app_id",
        "app_hash",
        "apiId",
        "apiHash",
        "proxy",
    }
)


def _deep_strip_sidecar_secrets(obj: object) -> bool:
    """Рекурсивно удалить из dict/list известные ключи секретов. Возвращает True, если что-то убрано."""
    changed = False
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if k in _SIDECAR_SECRET_KEYS:
                del obj[k]
                changed = True
            elif _deep_strip_sidecar_secrets(obj[k]):
                changed = True
    elif isinstance(obj, list):
        for item in obj:
            if _deep_strip_sidecar_secrets(item):
                changed = True
    return changed


def sanitize_session_sidecar_json_file(path: Path) -> tuple[bool, str | None]:
    """
    Прочитать один ``*.json`` в каталоге сессий, убрать api/proxy на всех уровнях вложенности.
    Возвращает (изменён ли файл, текст ошибки или None).
    """
    p = Path(path)
    if not p.is_file():
        return False, "не файл"
    try:
        raw = p.read_text(encoding="utf-8-sig").strip()
    except OSError as e:
        return False, str(e)
    if not raw:
        return False, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return False, e.msg
    if not isinstance(data, dict):
        return False, "ожидался объект JSON"
    if not _deep_strip_sidecar_secrets(data):
        return False, None
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return False, str(e)
    return True, None


def sanitize_all_session_sidecar_json_files(
    settings: Settings | None = None,
) -> tuple[int, int, list[str]]:
    """
    Обойти все ``*.json`` в каталоге сессий Telethon и убрать api/proxy рекурсивно.

    Возвращает (число изменённых файлов, число файлов с ошибкой, список сообщений).
    """
    s = settings or Settings()
    d = telethon_session_dir_path(s)
    if not d.is_dir():
        return 0, 0, []
    changed_n = 0
    err_n = 0
    errs: list[str] = []
    for path in sorted(d.glob("*.json")):
        ok, err = sanitize_session_sidecar_json_file(path)
        if err:
            err_n += 1
            errs.append(f"{path.name}: {err}")
        elif ok:
            changed_n += 1
    return changed_n, err_n, errs


def strip_proxy_from_accounts(
    settings: Settings | None = None,
) -> tuple[int, int, str]:
    """
    Убрать поле ``proxy`` у записей с ``session_name`` в accounts.json и в соответствующих sidecar.

    Возвращает (число строк accounts.json, где снят proxy, число изменённых sidecar, путь к accounts.json).
    """
    s = settings or Settings()
    all_rows = load_accounts_all()
    n_acc = 0
    n_side = 0
    for row in all_rows:
        if not isinstance(row, dict) or row.get("_template"):
            continue
        name = (row.get("session_name") or "").strip()
        if not name:
            continue
        if "proxy" in row:
            row.pop("proxy", None)
            n_acc += 1
        if remove_proxy_from_session_sidecar(name, s):
            n_side += 1
    save_accounts_all(all_rows)
    return n_acc, n_side, str(accounts_json_path())


def allowed_session_names_from_accounts() -> set[str]:
    """Имена сессий из accounts.json (не шаблон), для сопоставления с файлами на диске."""
    return {
        (r.get("session_name") or "").strip()
        for r in bundle_round_robin_account_rows(load_accounts_all())
        if (r.get("session_name") or "").strip()
    }


def delete_orphan_session_artifacts(
    settings: Settings | None = None,
    *,
    dry_run: bool = False,
) -> tuple[list[str], list[str]]:
    """
    Удалить ``*.session`` и одноимённые ``*.json``, если ``stem`` нет среди ``session_name`` в accounts.json.

    ``dry_run=True`` — только список путей, которые были бы удалены.

    Возвращает (список путей обработанных файлов, предупреждения).
    """
    s = settings or Settings()
    d = telethon_session_dir_path(s)
    warns: list[str] = []
    if not d.is_dir():
        return [], [f"Каталог сессий не найден: {d}"]
    allowed = allowed_session_names_from_accounts()
    deleted: list[str] = []
    stems: set[str] = set()
    for p in d.glob("*.session"):
        stems.add(p.stem)
    for p in d.glob("*.json"):
        stems.add(p.stem)
    for stem in sorted(stems):
        if stem in allowed:
            continue
        for suffix in (".session", ".json"):
            fp = d / f"{stem}{suffix}"
            if not fp.is_file():
                continue
            rel = str(fp.relative_to(d))
            if dry_run:
                deleted.append(f"(dry-run) {rel}")
                continue
            try:
                fp.unlink()
                deleted.append(rel)
            except OSError as e:
                warns.append(f"{rel}: {e}")
    return deleted, warns


def remove_account_rows_without_session_file(
    settings: Settings | None = None,
    *,
    dry_run: bool = False,
) -> tuple[int, list[str]]:
    """
    Удалить из accounts.json строки с ``session_name``, для которых нет файла ``<name>.session``.

    Возвращает (число удалённых строк, имена сессий).
    """
    s = settings or Settings()
    session_dir = telethon_session_dir_path(s)
    all_rows = load_accounts_all()
    removed_names: list[str] = []
    kept: list[dict] = []
    for row in all_rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        name = (row.get("session_name") or "").strip()
        if (
            name
            and not row.get("_template")
            and not (session_dir / f"{name}.session").is_file()
        ):
            removed_names.append(name)
            continue
        kept.append(row)
    if dry_run:
        return len(removed_names), removed_names
    if removed_names:
        save_accounts_all(kept)
    return len(removed_names), removed_names


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


def load_proxy_pool_from_file(path: Path) -> list[str]:
    """Прокси из произвольного txt (формат как config/proxies.txt)."""
    return _read_proxy_file(Path(path))


def parse_api_credentials_line(line: str) -> tuple[int, str] | None:
    """Одна строка ``api_id:api_hash`` (hash может содержать двоеточия — берём split только первый ``:``)."""
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None
    if ":" not in line:
        return None
    left, right = line.split(":", 1)
    ds = digits_only(left.strip())
    if not ds:
        return None
    ahash = right.strip()
    if not ahash:
        return None
    try:
        return int(ds), ahash
    except ValueError:
        return None


def load_api_pairs_from_file(path: Path) -> list[tuple[int, str]]:
    """Список (api_id, api_hash) из txt (пакет campaign/apis.txt)."""
    p = Path(path)
    if not p.is_file():
        return []
    out: list[tuple[int, str]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        pair = parse_api_credentials_line(raw)
        if pair:
            out.append(pair)
    return out


def assign_apis_round_robin_to_accounts(
    api_pairs: list[tuple[int, str]],
    settings: Settings | None = None,
) -> tuple[bool, str]:
    """
    Назначить api_id/api_hash из списка парам аккаунтам (round-robin).
    Обновляет accounts.json и при наличии sidecar ``sessions/<name>.json``.
    """
    if not api_pairs:
        return False, "Пустой список api (apis.txt)"
    s = settings or Settings()
    all_rows = load_accounts_all()
    targets = bundle_round_robin_account_rows(all_rows)
    if not targets:
        return False, "Нет аккаунтов (session_name) в accounts.json"
    for i, acc in enumerate(targets):
        aid, ahash = api_pairs[i % len(api_pairs)]
        acc["api_id"] = int(aid)
        acc["api_hash"] = str(ahash).strip()
        name = acc.get("session_name")
        if name:
            write_api_to_session_sidecar(str(name), aid, ahash, s)
    save_accounts_all(all_rows)
    return True, str(accounts_json_path())


def strip_api_credentials_from_accounts(
    settings: Settings | None = None,
) -> tuple[int, int, str]:
    """
    Убрать ключи приложения у всех аккаунтов с ``session_name`` в accounts.json
    и в sidecar ``sessions/<name>.json`` (в т.ч. app_id/app_hash на верхнем уровне).

    Удобно перед коммитом/push, чтобы не утекли api_id/api_hash. После клона назначьте API
    из ``apis.txt`` (рассылка из пакета) или задайте ``telethon_default_api`` и синхронизацию.

    Возвращает (число строк accounts.json, где сняты поля, число изменённых sidecar, путь к accounts.json).
    """
    s = settings or Settings()
    all_rows = load_accounts_all()
    n_acc = 0
    n_side = 0
    for row in all_rows:
        if not isinstance(row, dict) or row.get("_template"):
            continue
        name = (row.get("session_name") or "").strip()
        if not name:
            continue
        popped = False
        for k in ("api_id", "api_hash"):
            if k in row:
                row.pop(k, None)
                popped = True
        if popped:
            n_acc += 1
        if remove_api_from_session_sidecar(name, s):
            n_side += 1
    save_accounts_all(all_rows)
    return n_acc, n_side, str(accounts_json_path())


def clear_telethon_default_api() -> tuple[bool, str]:
    """Обнулить ``telethon_default_api`` в settings.json (остальные ключи сохраняются)."""
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
    data["telethon_default_api"] = {"api_id": None, "api_hash": ""}
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        return False, f"Не удалось записать settings.json: {e}"
    return True, str(path)


def clear_accounts_json() -> None:
    """Очистить config/accounts.json (пустой список)."""
    save_accounts_all([])


def wipe_telethon_session_files(settings: Settings | None = None) -> int:
    """Удалить все ``*.session`` и одноимённые ``*.json`` sidecar в каталоге сессий."""
    d = telethon_session_dir_path(settings)
    n = 0
    for p in list(d.glob("*.session")):
        stem = p.stem
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
        sidecar = d / f"{stem}.json"
        if sidecar.is_file():
            try:
                sidecar.unlink()
            except OSError:
                pass
    return n


def clear_proxy_pool_in_config() -> tuple[bool, str]:
    """Очистить config/proxies.txt и ``proxies.list`` в settings.json (если файл есть)."""
    cfg_dir = _config_dir()
    ptxt = cfg_dir / "proxies.txt"
    try:
        ptxt.write_text("", encoding="utf-8")
    except OSError as e:
        return False, f"proxies.txt: {e}"
    path = settings_json_path()
    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8-sig").strip()
            data = json.loads(raw) if raw else {}
            if isinstance(data, dict) and isinstance(data.get("proxies"), dict):
                data["proxies"]["list"] = []
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        except (OSError, TypeError, json.JSONDecodeError):
            pass
    return True, str(ptxt)
