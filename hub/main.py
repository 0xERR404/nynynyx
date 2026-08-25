import logging
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.cleanup import start_cleanup_watchdog
from core.manifest import load_enabled_manifest, load_router_module
from core.module_supervisor import (
    list_module_names, proxy_to_module, set_new_module_callback, spawn_module, start_watchdog,
)
from core.restart import register_start_and_check_safe_mode, reset_crash_counter
from routes import status as status_route, modules as modules_route

BASE_DIR = Path(__file__).resolve().parent
MODULES_DIR = BASE_DIR / "modules"
NYX_DIR = MODULES_DIR / "nyx"
INTERFACE_DIR = MODULES_DIR / "interface"
LOG_DIR = Path("/app/shared/logs")

# nyx и interface — не обычные модули: nyx монтируется в общем процессе с
# хабом (нужен доступ к core/ и памяти, плюс должна жить даже в safe mode),
# interface — статика, а не router. Оба физически лежат в modules/, просто
# spawn_user_modules их пропускает (изолировать в subprocess незачем/нельзя).
RESERVED_MODULE_NAMES = {"nyx", "interface"}
NYX_INBOX = NYX_DIR / "data" / "inbox"
NYX_OUTBOX = NYX_DIR / "data" / "outbox"

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(LOG_DIR / "hub.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"),
    ],
)
logger = logging.getLogger("nexus404.hub")

app = FastAPI(title="NEXUS404 Hub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(status_route.router, prefix="/api")
app.include_router(modules_route.router, prefix="/api")


def install_requirements() -> None:
    """Она пишет пакет в modules/<имя>/requirements.txt — ставим до спавна процессов."""
    if not MODULES_DIR.exists():
        return
    for req_file in sorted(MODULES_DIR.glob("*/requirements.txt")):
        logger.info(f"[deps] найден {req_file.relative_to(MODULES_DIR)}, ставлю...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", str(req_file)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.error(f"[deps] {req_file.relative_to(MODULES_DIR)}: pip install упал:\n{result.stderr[-2000:]}")


def mount_nyx() -> None:
    """modules/nyx/router.py — её код чата, монтируется в общем процессе с
    хабом (не изолируется в subprocess), доступен даже в safe mode. Сама
    папка — обычная modules/nyx/, но router.py/manifest.json в write-денилисте
    apply_file_edit (core/tools.py) — редактировать может только git."""
    manifest, reason = load_enabled_manifest(NYX_DIR)
    if reason == "invalid_manifest":
        logger.error("[nyx] битый manifest.json — чат не запустится")
        return
    if manifest is None:
        logger.error("[nyx] manifest.json/router.py не найдены — чат не запустится")
        return

    try:
        mod = load_router_module(NYX_DIR / "router.py", "nyx_router")
    except Exception as e:
        logger.error(f"[nyx] ошибка загрузки router.py — {e}")
        return

    if not hasattr(mod, "router"):
        logger.error("[nyx] router.py не содержит объект `router` — чат не запустится")
        return

    app.include_router(mod.router, prefix="/api/nyx")
    logger.info(f"[nyx] подключён v{manifest.get('version', '0.0.0')}")


def spawn_user_modules(directory: Path) -> None:
    """Каждый модуль в своём subprocess — падение одного не задевает остальное."""
    if not directory.exists():
        return
    for module_dir in sorted(directory.iterdir()):
        if not module_dir.is_dir() or module_dir.name.startswith(("_", ".")):
            continue
        if module_dir.name in RESERVED_MODULE_NAMES:
            continue

        manifest, reason = load_enabled_manifest(module_dir)
        if reason == "invalid_manifest":
            logger.warning(f"[modules] {module_dir.name}: битый manifest.json, пропускаю")
        if manifest is None:
            continue

        spawn_module(module_dir.name, module_dir)


def _static_mount_index() -> int | None:
    for i, r in enumerate(app.router.routes):
        if getattr(r, "name", None) == "static":
            return i
    return None


def _register_proxy_route(name: str) -> None:
    """Форвардит все методы/пути под /api/<name>/... в процесс модуля. Вставляет
    роут ПЕРЕД StaticFiles на '/', если она уже подключена (hot-reload после
    старта) — иначе catch-all статика перехватит путь раньше, чем прокси."""
    from fastapi.routing import APIRoute

    async def _proxy(rest_path: str, request: Request, _name=name):
        return await proxy_to_module(_name, rest_path, request)

    async def _proxy_root(request: Request, _name=name):
        return await proxy_to_module(_name, "", request)

    route1 = APIRoute(
        "/api/" + name + "/{rest_path:path}", _proxy,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"], name=f"proxy_{name}",
    )
    route2 = APIRoute(
        "/api/" + name, _proxy_root,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"], name=f"proxy_{name}_root",
    )

    idx = _static_mount_index()
    if idx is None:
        app.router.routes.append(route1)
        app.router.routes.append(route2)
    else:
        app.router.routes.insert(idx, route1)
        app.router.routes.insert(idx + 1, route2)


# Разрешаем hot-reload регистрировать роут нового модуля на лету (без рестарта
# хаба) в любой момент — не только на старте. Регистрируем колбэк всегда,
# даже в safe mode: чат (единственное, что там работает) всё ещё может
# писать новые модули через apply_file_edit, они должны подниматься.
set_new_module_callback(_register_proxy_route)


safe_mode = register_start_and_check_safe_mode()

# Чат — ВСЕГДА первым, до статики. StaticFiles на "/" ниже перехватывает
# вообще любой путь (он начинается с "/"), поэтому всё, что должно быть
# доступно как API, обязано быть подключено ДО этого mount — иначе
# Starlette отдаст /api/nyx/* статике и ничего не найдёт.
mount_nyx()
start_cleanup_watchdog([NYX_INBOX, NYX_OUTBOX])

if safe_mode:
    logger.error(
        "[safe-mode] хаб падал слишком часто подряд — остальные modules/ НЕ монтируются "
        "в этом запуске. Доступен только чат с Никс (/api/nyx/ui). Попроси её (или сам) "
        "откати последнюю правку и перезапусти хаб вручную, чтобы выйти из safe mode."
    )
else:
    install_requirements()
    spawn_user_modules(MODULES_DIR)
    for _name in list_module_names():
        _register_proxy_route(_name)
    start_watchdog()
    # StaticFiles на "/" — обязательно ПОСЛЕДНИМ добавляемым маршрутом
    if INTERFACE_DIR.exists():
        app.mount("/", StaticFiles(directory=str(INTERFACE_DIR), html=True), name="static")
    threading.Timer(180, reset_crash_counter).start()
