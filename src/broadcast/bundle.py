"""Пакет рассылки: каталог с zip сессий, apis.txt, proxies.txt, текстами и картинками."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.config import load_proxy_pool_from_file


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


def load_campaign_bundle(root: Path, zip_name: str = "accounts.zip") -> CampaignBundle:
    r = Path(root).expanduser().resolve()
    return CampaignBundle(
        root=r,
        zip_path=r / zip_name,
        apis_file=r / "apis.txt",
        proxies_file=r / "proxies.txt",
        text_1=r / "text_1.txt",
        text_2=r / "text_2.txt",
        image_1=r / "1.jpg",
        image_2=r / "2.jpg",
        image_3=r / "3.jpg",
    )


def validate_campaign_bundle(b: CampaignBundle) -> list[str]:
    """Список ошибок; пустой — ок."""
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
    if not b.proxies_file.is_file():
        errs.append(f"Нет {b.proxies_file.name} (рядом с {b.apis_file.name} в {b.root})")
    else:
        pool = load_proxy_pool_from_file(b.proxies_file)
        if not pool:
            errs.append(
                f"{b.proxies_file.name}: нет ни одной валидной строки "
                f"(формат host:port:user:pass → http, или готовый URL socks5/http)"
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
    for i, p in enumerate(b.image_paths, start=1):
        if not p.is_file():
            errs.append(f"Нет {i}.jpg")
    return errs


def read_campaign_texts(b: CampaignBundle) -> tuple[str, str]:
    t1 = b.text_1.read_text(encoding="utf-8").strip()
    t2 = b.text_2.read_text(encoding="utf-8").strip()
    return t1, t2
