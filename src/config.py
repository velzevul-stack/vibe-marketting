"""Конфигурация приложения."""
import json
from pathlib import Path


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
        self.telegram_index_api_key: str | None = self._data.get("telegram_index_api_key") or None



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


def _read_proxy_file(filepath: Path) -> list[str]:
    """Прочитать прокси из файла."""
    if not filepath.exists():
        return []
    return [
        l.strip() for l in filepath.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]


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
        result.extend(p for p in proxy_list if isinstance(p, str) and p.strip())

    if not result:
        path = config_dir / "proxies.txt"
        result = _read_proxy_file(path)

    return result


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

    @property
    def proxies(self) -> list[str]:
        return self._proxies.copy()


def load_accounts() -> list[dict]:
    """Загрузить аккаунты из accounts.json."""
    path = Path(__file__).parent.parent / "config" / "accounts.json"
    data = load_json(path)
    if not isinstance(data, list):
        return []
    return [a for a in data if isinstance(a, dict) and a.get("api_id") and a.get("api_hash")]
