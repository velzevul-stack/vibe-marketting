"""Оркестрация фаз Web и my.telegram.org."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm, Prompt

from src.cli_input import digits_only, strip_c0_controls
from src.config import (
    Settings,
    effective_2fa_password,
    is_placeholder_proxy_url,
    load_accounts_all,
    load_proxy_pool_from_config,
    normalize_proxy_line,
    telethon_session_dir_path,
    touch_accounts_last_use,
    upsert_telethon_account,
    write_api_to_session_sidecar,
)
from src.mytelegram_portal.portal_flow import run_mytelegram_portal
from src.mytelegram_portal.pw_util import launch_browser, playwright_proxy_from_url
from src.mytelegram_portal.state import (
    AccountJob,
    PortalState,
    default_state_path,
    load_portal_state,
    save_portal_state,
    storage_dir,
)
from src.mytelegram_portal.web_telegram_login import run_telegram_web_login

Mode = Literal["phase1", "phase2", "full"]


def _project_root(console: Console | None = None) -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _log_line(console: Console, session: str, msg: str) -> None:
    console.print(f"[dim][mytg][/] [cyan]{escape(session)}[/] {escape(msg)}")


def phone_e164_from_session_stem(stem: str) -> str | None:
    """
    Имя файла сессии без расширения → телефон для Web/Telegram (+E.164).

    Допускается ведущий ``+`` (как в ``+123456789012.session``), иначе только цифры.
    Суффикс коллизии: ``+375…_a3f2`` или ``375…_a3f2`` → номер до ``_``.
    """
    raw = (stem or "").strip()
    if not raw:
        return None
    base = raw.split("_", 1)[0].strip()
    d = digits_only(base)
    if len(d) < 8 or len(d) > 15:
        return None
    return f"+{d}"


def collect_jobs_from_session_files(
    settings: Settings,
    console: Console,
) -> list[AccountJob]:
    """
    Список задач: каждый ``*.session`` в ``telethon_session_dir``;
    имя = E.164 в цифрах, опционально с ``+`` в начале (напр. ``+123….session``);
    прокси — round-robin из пула (proxies.txt / settings), без placeholder.
    """
    pool = [
        p
        for p in load_proxy_pool_from_config()
        if p and not is_placeholder_proxy_url(p)
    ]
    if not pool:
        console.print(
            "[red]Пул прокси пуст или только заглушки.[/] Заполните [cyan]config/proxies.txt[/] "
            "или [cyan]proxies.list[/] в settings.json."
        )
        return []
    sess_dir = telethon_session_dir_path(settings)
    paths = sorted(sess_dir.glob("*.session"), key=lambda p: p.stem.lower())
    jobs: list[AccountJob] = []
    for i, p in enumerate(paths):
        stem = p.stem
        phone = phone_e164_from_session_stem(stem)
        if not phone:
            _log_line(
                console,
                stem,
                "пропуск: после +/_ не 8–15 цифр E.164 в имени файла",
            )
            continue
        raw_px = pool[i % len(pool)]
        px = normalize_proxy_line(raw_px) or raw_px
        if "://" not in px and px:
            px = f"http://{px}"
        jobs.append(
            AccountJob(
                session_name=stem,
                phone=phone,
                proxy_url=px,
                status="pending",
            )
        )
    return jobs


def collect_jobs_from_accounts(console: Console, project_root: Path) -> list[AccountJob]:
    rows = load_accounts_all()
    jobs: list[AccountJob] = []
    for r in rows:
        if not isinstance(r, dict) or r.get("_template"):
            continue
        sn = (r.get("session_name") or "").strip()
        phone = (r.get("phone") or "").strip()
        raw_px = (r.get("proxy") or "").strip()
        if not sn or not phone:
            continue
        if not raw_px or is_placeholder_proxy_url(raw_px):
            _log_line(console, sn or "?", "пропуск: нет прокси в accounts.json")
            continue
        px = normalize_proxy_line(raw_px) or raw_px
        if "://" not in px and px:
            px = f"http://{px}"
        jobs.append(
            AccountJob(
                session_name=sn,
                phone=phone,
                proxy_url=px,
                status="pending",
            )
        )
    return jobs


def _merge_state_with_jobs(
    state: PortalState | None, jobs: list[AccountJob]
) -> PortalState:
    if not state or not state.accounts:
        return PortalState(accounts=list(jobs))
    by_sn = {a.session_name: a for a in state.accounts}
    merged: list[AccountJob] = []
    for j in jobs:
        old = by_sn.get(j.session_name)
        if old and old.status in ("web_ok", "portal_pending", "api_ok"):
            merged.append(old)
        else:
            merged.append(j)
    for sn, old in by_sn.items():
        if sn not in {x.session_name for x in jobs}:
            merged.append(old)
    return PortalState(accounts=merged)


def _wait_interactive(console: Console, seconds: float) -> None:
    sec = max(0, int(seconds))
    if sec <= 0:
        return
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        t = progress.add_task(f"Пауза {sec}s (my.telegram.org)…", total=sec)
        for _ in range(sec):
            time.sleep(1)
            progress.update(t, advance=1)


def run_mytg_menu_flow(
    console: Console,
    *,
    settings: Settings | None = None,
    project_root: Path | None = None,
    state_path: Path | None = None,
    mode: Mode = "full",
    jobs_override: list[AccountJob] | None = None,
    from_session_files: bool = False,
    max_accounts: int | None = None,
) -> int:
    """
    mode:
      phase1 — только Telegram Web + storage_state;
      phase2 — только my.telegram.org (нужен state с web_ok);
      full — фаза 1, опциональная пауза, фаза 2.

    ``from_session_files`` — брать номер из имени ``*.session`` (8–15 цифр), прокси RR из config.
    ``max_accounts`` — не больше первых N задач в порядке списка (None = все).
    """
    sett = settings or Settings()
    root = project_root or _project_root()
    spath = state_path or default_state_path(root)

    try:
        import playwright  # noqa: F401
    except ImportError:
        console.print(
            "[red]Playwright не установлен.[/] [dim]pip install playwright[/] и "
            "[dim]playwright install chromium[/]"
        )
        return 1

    if jobs_override is not None:
        jobs = jobs_override
    elif from_session_files:
        jobs = collect_jobs_from_session_files(sett, console)
    else:
        jobs = collect_jobs_from_accounts(console, root)
    if not jobs:
        if from_session_files:
            console.print(
                "[yellow]Нет подходящих *.session[/] в папке сессий "
                f"[cyan]{telethon_session_dir_path(sett)}[/] "
                "[dim](имя = 8–15 цифр номера, можно с ведущим +)[/]."
            )
        else:
            console.print(
                "[yellow]Нет строк в accounts.json[/] с [bold]session_name[/] + [bold]phone[/] + [bold]proxy[/]."
            )
        return 1

    if max_accounts is not None and max_accounts > 0:
        before = len(jobs)
        jobs = jobs[:max_accounts]
        console.print(
            f"[dim]За прогон:[/] [cyan]{len(jobs)}[/] из [cyan]{before}[/] "
            f"[dim](ограничение max_accounts)[/]"
        )

    prev = load_portal_state(spath)
    state = _merge_state_with_jobs(prev, jobs)
    trim_state = jobs_override is not None or max_accounts is not None
    if trim_state:
        allowed = {j.session_name for j in jobs}
        state.accounts = [a for a in state.accounts if a.session_name in allowed]
    save_portal_state(spath, state)

    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    ]

    def run_phase1() -> None:
        nonlocal state
        store = storage_dir(root)
        with sync_playwright() as p:
            browser = launch_browser(p, sett)
            try:
                for job in state.accounts:
                    if job.status != "pending":
                        continue
                    proxy = playwright_proxy_from_url(job.proxy_url)
                    ctx_kwargs: dict = {
                        "viewport": {"width": 1280, "height": 800},
                        "user_agent": user_agents[hash(job.session_name) % len(user_agents)],
                    }
                    if proxy:
                        ctx_kwargs["proxy"] = proxy
                    context = browser.new_context(**ctx_kwargs)
                    page = context.new_page()
                    lg = lambda m: _log_line(console, job.session_name, m)
                    try:
                        run_telegram_web_login(
                            page,
                            session_name=job.session_name,
                            phone=job.phone,
                            settings=sett,
                            log=lg,
                            prompt_login_code=lambda ph: strip_c0_controls(
                                Prompt.ask(f"Код входа Telegram для {ph}")
                            ),
                            get_2fa_password=lambda: effective_2fa_password(sett),
                        )
                        out = store / f"{job.session_name}.json"
                        context.storage_state(path=str(out))
                        job.web_storage_path = str(out.resolve())
                        job.status = "web_ok"
                        job.wait_until_ts = (
                            time.time() + sett.mytg_wait_after_web_sec
                            if sett.mytg_wait_after_web_sec > 0
                            else None
                        )
                        job.last_error = None
                        lg(f"storage_state → {job.web_storage_path}")
                    except Exception as e:
                        job.status = "failed"
                        job.last_error = str(e)
                        console.print(f"[red][mytg][/] {job.session_name}: {e}[/]")
                    finally:
                        context.close()
                        save_portal_state(spath, state)
            finally:
                browser.close()

    def run_phase2() -> None:
        nonlocal state
        with sync_playwright() as p:
            browser = launch_browser(p, sett)
            try:
                for job in state.accounts:
                    if job.status != "web_ok":
                        continue
                    if job.wait_until_ts and time.time() < job.wait_until_ts:
                        left = int(job.wait_until_ts - time.time())
                        _log_line(
                            console,
                            job.session_name,
                            f"пропуск фазы 2: ждать ещё ~{left}s (wait_until)",
                        )
                        continue
                    if not job.web_storage_path or not Path(job.web_storage_path).is_file():
                        job.status = "failed"
                        job.last_error = "нет web_storage_path"
                        save_portal_state(spath, state)
                        continue
                    proxy = playwright_proxy_from_url(job.proxy_url)
                    ctx_kwargs: dict = {
                        "storage_state": job.web_storage_path,
                        "viewport": {"width": 1280, "height": 800},
                        "user_agent": user_agents[hash(job.session_name) % len(user_agents)],
                    }
                    if proxy:
                        ctx_kwargs["proxy"] = proxy
                    context = browser.new_context(**ctx_kwargs)
                    lg = lambda m, sn=job.session_name: _log_line(console, sn, m)
                    try:
                        api_id, api_hash = run_mytelegram_portal(
                            context,
                            job,
                            sett,
                            lg,
                            prompt_portal_code=lambda: strip_c0_controls(
                                Prompt.ask(
                                    "Код подтверждения my.telegram.org "
                                    "[dim](если не подхватили из Web)[/]"
                                )
                            ),
                        )
                        upsert_telethon_account(
                            job.session_name,
                            api_id,
                            api_hash,
                            phone=job.phone,
                            proxy=job.proxy_url,
                        )
                        write_api_to_session_sidecar(
                            job.session_name, api_id, api_hash, sett
                        )
                        job.status = "api_ok"
                        job.last_error = None
                        touch_accounts_last_use([job.session_name], kind="mytg")
                        lg(f"api_id={api_id} записан в accounts.json")
                    except Exception as e:
                        job.status = "failed"
                        job.last_error = str(e)
                        console.print(f"[red][mytg][/] {job.session_name}: {e}[/]")
                    finally:
                        context.close()
                        save_portal_state(spath, state)
            finally:
                browser.close()

    if mode == "phase1":
        run_phase1()
        return 0
    if mode == "phase2":
        run_phase2()
        return 0

    # full
    run_phase1()
    if sett.mytg_wait_after_web_sec > 0:
        if Confirm.ask(
            f"Ждать {sett.mytg_wait_after_web_sec / 3600:.1f} ч перед my.telegram.org здесь?",
            default=False,
        ):
            _wait_interactive(console, sett.mytg_wait_after_web_sec)
        else:
            console.print(
                f"[yellow]Запустите позже:[/] [dim]python main.py --mytg-phase2 "
                f"--mytg-state {spath}[/]"
            )
            return 0
    run_phase2()
    return 0


def run_mytg_cli(
    *,
    mode: Mode,
    state_path: Path | None,
    project_root: Path | None = None,
    from_session_files: bool = False,
) -> int:
    from rich.console import Console

    con = Console()
    root = project_root or _project_root()
    sp = state_path or default_state_path(root)
    return run_mytg_menu_flow(
        con,
        project_root=root,
        state_path=sp,
        mode=mode,
        from_session_files=from_session_files,
    )
