"""Пакет рассылки: каталог с zip сессий, apis.txt, proxy.txt/proxies.txt, текстами и картинками."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.config import load_proxy_pool_from_file, normalize_proxy_line, parse_api_credentials_line

APIS_SESSIONS_FILENAME = "apis_sessions.txt"
SESSIONS_BIND_FILENAME = "sessions_bind.txt"


@dataclass(frozen=True)
class CampaignBundle:
    """Пути к файлам пакета (корень каталога — произвольный)."""

    root: Path
    zip_path: Path
    apis_file: Path
    proxies_file: Path
    text_1: Path
    text_2: Path
    image_1: Path
    image_2: Path
    image_3: Path

    @property
    def image_paths(self) -> tuple[Path, Path, Path]:
        return (self.image_1, self.image_2, self.image_3)


@dataclass(frozen=True)
class CampaignImportSlice:
    """Один ZIP + соответствующие apis/proxies (базовый accounts.zip или accounts2.zip + apis2.txt + proxies2.txt)."""

    label: str
    zip_path: Path
    apis_file: Path
    proxies_file: Path


def resolve_campaign_proxy_pool_path(root: Path, slice_n: int | None) -> Path:
    """
    Пул прокси для round-robin: предпочтение ``proxy.txt`` / ``proxyN.txt``,
    иначе прежние ``proxies.txt`` / ``proxiesN.txt``.
    """
    r = Path(root).expanduser().resolve()
    if slice_n is None:
        preferred = r / "proxy.txt"
        fallback = r / "proxies.txt"
    else:
        preferred = r / f"proxy{slice_n}.txt"
        fallback = r / f"proxies{slice_n}.txt"
    if preferred.is_file():
        try:
            if load_proxy_pool_from_file(preferred):
                return preferred
        except OSError:
            pass
    return fallback


def discover_campaign_import_slices(root: Path) -> list[CampaignImportSlice]:
    """
    Сначала базовый accounts.zip + apis.txt + proxy.txt (или proxies.txt),
    затем accountsN.zip + apisN.txt + proxyN.txt (или proxiesN.txt).
    """
    r = Path(root).expanduser().resolve()
    out: list[CampaignImportSlice] = [
        CampaignImportSlice(
            label="accounts",
            zip_path=r / "accounts.zip",
            apis_file=r / "apis.txt",
            proxies_file=resolve_campaign_proxy_pool_path(r, None),
        )
    ]
    found: list[tuple[int, Path]] = []
    for p in r.glob("accounts*.zip"):
        m = re.match(r"(?i)^accounts(\d+)\.zip$", p.name)
        if not m:
            continue
        n = int(m.group(1))
        found.append((n, p))
    found.sort(key=lambda x: x[0])
    for n, zp in found:
        out.append(
            CampaignImportSlice(
                label=f"accounts{n}",
                zip_path=zp,
                apis_file=r / f"apis{n}.txt",
                proxies_file=resolve_campaign_proxy_pool_path(r, n),
            )
        )
    return out


def validate_import_slice_apis_proxies(sl: CampaignImportSlice) -> list[str]:
    """Проверка apis/proxy-пула для слайса (zip считается уже существующим)."""
    errs: list[str] = []
    if not sl.apis_file.is_file():
        errs.append(f"Нет {sl.apis_file.name} для {sl.zip_path.name} в {sl.zip_path.parent}")
    else:
        n_api = 0
        for raw in sl.apis_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            left, right = line.split(":", 1)
            if left.strip().isdigit() and right.strip():
                n_api += 1
        if n_api < 1:
            errs.append(f"{sl.apis_file.name}: нет ни одной валидной пары api_id:api_hash")
    root = sl.zip_path.parent
    if sl.label == "accounts":
        p_new, p_old = root / "proxy.txt", root / "proxies.txt"
    else:
        m = re.match(r"(?i)^accounts(\d+)$", sl.label)
        n = int(m.group(1)) if m else 0
        p_new, p_old = root / f"proxy{n}.txt", root / f"proxies{n}.txt"
    pool = load_proxy_pool_from_file(sl.proxies_file) if sl.proxies_file.is_file() else []
    if not pool:
        pool = load_proxy_pool_from_file(p_new) if p_new.is_file() else []
    if not pool:
        pool = load_proxy_pool_from_file(p_old) if p_old.is_file() else []
    if not pool:
        errs.append(
            f"Нет валидного пула прокси для {sl.zip_path.name}: "
            f"создайте {p_new.name} или {p_old.name} (host:port:user:pass или URL)"
        )
    return errs


def validate_extra_import_slices(slices: list[CampaignImportSlice]) -> list[str]:
    """Доп. пакеты accounts2.zip…: если ZIP есть — обязательны apisN и proxiesN."""
    errs: list[str] = []
    for sl in slices[1:]:
        if not sl.zip_path.is_file():
            continue
        errs.extend(validate_import_slice_apis_proxies(sl))
    return errs


def load_campaign_bundle(root: Path, zip_name: str = "accounts.zip") -> CampaignBundle:
    r = Path(root).expanduser().resolve()
    return CampaignBundle(
        root=r,
        zip_path=r / zip_name,
        apis_file=r / "apis.txt",
        proxies_file=resolve_campaign_proxy_pool_path(r, None),
        text_1=r / "text_1.txt",
        text_2=r / "text_2.txt",
        image_1=r / "1.jpg",
        image_2=r / "2.jpg",
        image_3=r / "3.jpg",
    )


def validate_campaign_bundle(b: CampaignBundle, *, require_images: bool = True) -> list[str]:
    """Список ошибок; пустой — ок. require_images=False — только text_1/2, без 1.jpg–3.jpg."""
    errs: list[str] = []
    if not b.root.is_dir():
        errs.append(f"Нет каталога: {b.root}")
        return errs
    if not b.zip_path.is_file():
        errs.append(f"Нет архива сессий: {b.zip_path.name} в {b.root}")
    if not b.apis_file.is_file():
        errs.append(f"Нет {b.apis_file.name} (строки api_id:api_hash)")
    else:
        n = 0
        for raw in b.apis_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            left, right = line.split(":", 1)
            if left.strip().isdigit() and right.strip():
                n += 1
        if n < 1:
            errs.append("apis.txt: нет ни одной валидной пары api_id:api_hash")
    p_new, p_old = b.root / "proxy.txt", b.root / "proxies.txt"
    pool = load_proxy_pool_from_file(b.proxies_file) if b.proxies_file.is_file() else []
    if not pool and p_new.is_file():
        pool = load_proxy_pool_from_file(p_new)
    if not pool and p_old.is_file():
        pool = load_proxy_pool_from_file(p_old)
    if not pool:
        errs.append(
            f"Нет валидного пула прокси в {b.root.name}: создайте {p_new.name} или {p_old.name} "
            f"(host:port:user:pass или URL socks5/http)"
        )
    for label, p in (
        ("text_1.txt", b.text_1),
        ("text_2.txt", b.text_2),
    ):
        if not p.is_file():
            errs.append(f"Нет {label}")
        else:
            if not p.read_text(encoding="utf-8").strip():
                errs.append(f"Пустой файл: {label}")
    if require_images:
        for i, p in enumerate(b.image_paths, start=1):
            if not p.is_file():
                errs.append(f"Нет {i}.jpg")
    return errs


def read_campaign_texts(b: CampaignBundle) -> tuple[str, str]:
    t1 = b.text_1.read_text(encoding="utf-8").strip()
    t2 = b.text_2.read_text(encoding="utf-8").strip()
    return t1, t2


def parse_apis_sessions_file(path: Path) -> tuple[dict[str, tuple[int, str]], list[str]]:
    """
    Файл ``apis_sessions.txt``: строки ``api_id:api_hash stem1 stem2 ...``.
    Возвращает (stem -> (api_id, api_hash), список ошибок). При дубликате стема — ошибка.
    """
    p = Path(path)
    if not p.is_file():
        return {}, []
    mapping: dict[str, tuple[int, str]] = {}
    errs: list[str] = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return {}, [f"{p.name}: не прочитать файл: {e}"]
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            if parse_api_credentials_line(line):
                errs.append(
                    f"{p.name}:{i}: после api_id:api_hash нужны стемы сессий (имена без .session)"
                )
            else:
                errs.append(f"{p.name}:{i}: ожидается ``api_id:api_hash stem1 stem2 ...``")
            continue
        cred = parse_api_credentials_line(parts[0])
        if not cred:
            errs.append(f"{p.name}:{i}: неверная пара api_id:api_hash ({parts[0]!r})")
            continue
        for st in parts[1:]:
            stem = st.strip()
            if not stem:
                continue
            if stem in mapping:
                errs.append(f"{p.name}: session stem {stem!r} встречается дважды")
                continue
            mapping[stem] = cred
    return mapping, errs


def parse_sessions_bind_file(
    path: Path,
) -> tuple[list[tuple[tuple[int, str], str, str]], list[str]]:
    """
    Файл ``sessions_bind.txt``: по строке на аккаунт — три поля через пробелы::

        api_id:api_hash <прокси_одной_строкой> <stem_сессии>

    Прокси без пробелов: ``socks5://...`` или ``host:port:user:pass``.
    Возвращает список ``((api_id, api_hash), proxy_url, session_stem)`` и ошибки.
    """
    p = Path(path)
    if not p.is_file():
        return [], []
    out: list[tuple[tuple[int, str], str, str]] = []
    errs: list[str] = []
    seen_stems: set[str] = set()
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return [], [f"{p.name}: не прочитать файл: {e}"]
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        if len(parts) != 3:
            errs.append(
                f"{p.name}:{i}: ожидается ровно 3 поля: "
                f"api_id:api_hash <прокси> <stem> — получено {len(parts)}"
            )
            continue
        cred_s, proxy_raw, stem = parts[0], parts[1].strip(), parts[2].strip()
        cred = parse_api_credentials_line(cred_s)
        if not cred:
            errs.append(f"{p.name}:{i}: неверная пара api_id:api_hash ({cred_s!r})")
            continue
        norm = normalize_proxy_line(proxy_raw)
        if not norm:
            errs.append(f"{p.name}:{i}: неверная строка прокси ({proxy_raw!r})")
            continue
        if not stem:
            errs.append(f"{p.name}:{i}: пустой stem сессии")
            continue
        if stem in seen_stems:
            errs.append(f"{p.name}: stem {stem!r} встречается дважды")
            continue
        seen_stems.add(stem)
        out.append((cred, norm, stem))
    return out, errs
