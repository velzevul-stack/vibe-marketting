"""Конфигурация приложения."""
import json
from pathlib import Path
from urllib.parse import quote


def _config_dir() -> Path:
    return Path(__file__).parent.parent / "config"


def _load_settings() -> dict:
    """Загрузить settings.json."""
    path = _config_dir() / "settings.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in (data or {}).items() if not k.startswith("_")}


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
        self.telegram_index_api_key: str | None = self._data.get("telegram_index_api_key") or None
        self.ddgs_search_enabled: bool = self._data.get("ddgs_search_enabled", True)
        self.tg_catalog_enabled: bool = self._data.get("tg_catalog_enabled", True)
        self.tgstat_token: str | None = self._data.get("tgstat_token") or None
        self.telemetr_api_key: str | None = self._data.get("telemetr_api_key") or None
        # Папка с *.session (относительно корня проекта / cwd), например "accounts" или "sessions"
        self.telethon_session_dir: str = self._data.get("telethon_session_dir", "sessions")
        # Для массовой подготовки аккаунтов (п. b): пароль облачного 2FA; лучше задать в settings.json локально
        self.bulk_2fa_password: str | None = (self._data.get("bulk_2fa_password") or None)
        self.bulk_prepare_delay_sec: float = float(
            self._data.get("bulk_prepare_delay_sec", 5.0)
        )
        # True: при каждом запуске меню сначала round-robin прокси из пула → accounts.json
        self.assign_proxies_on_startup: bool = bool(
            self._data.get("assign_proxies_on_startup", False)
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


def assign_proxies_round_robin_to_accounts() -> tuple[bool, str]:
    """
    Назначить прокси из пула аккаунтам (round-robin).
    Сохраняет весь accounts.json (включая служебные строки), не только список аккаунтов.
    """
    proxies = load_proxies()
    if not proxies:
        return False, "Нет прокси в пуле (proxies.txt / settings.json)"
    all_rows = load_accounts_all()
    tele = load_accounts()
    if not tele:
        return False, "Нет аккаунтов в accounts.json"
    for i, acc in enumerate(tele):
        acc["proxy"] = proxies[i % len(proxies)]
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


def load_proxies() -> list[str]:
    """Загрузить прокси из settings.json (files или list) или из proxies.txt по умолчанию."""
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
    try:
        api_id = int(aid_s)
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
