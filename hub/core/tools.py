"""
Инструменты DeepSeek function calling.

Чтение (list_files/read_file) — весь репозиторий, read-only, кроме денилиста.
Запись (apply_file_edit) — только внутри modules/, без деления на зоны: путь
вида "nyx/facts.json", "interface/app.js", "notes/router.py". Остальное
(core/, routes/, main.py, docker-compose.yml, scripts/) недоступно на запись
физически — не входит в MODULES_DIR. Её собственный код (nyx/router.py,
nyx/manifest.json) физически лежит внутри modules/, но в write-денилисте —
защита не расположением, а конкретным списком путей.

Перед записью: router.py проверяется на синтаксис (ast.parse), manifest.json —
на обязательные поля. Не проходит — файл не трогаем, возвращаем ошибку.
Каждая правка бэкапится в <файл>.bak и логируется в edits_log.json,
revert_file_edit откатывает последнюю правку конкретного файла.

Правка router.py/manifest.json/requirements.txt пользовательского модуля НЕ
перезапускает хаб — поднимается/пересобирается только subprocess этого
модуля (core/module_supervisor.reload_or_spawn_module). Полный рестарт хаба
для этого не нужен в принципе: единственный код, ради которого он был бы
нужен (её же собственный router.py) сюда через apply_file_edit не пишется —
он в WRITE_DENYLIST_RELATIVE ниже.
"""

import ast
import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from core.system_stats import get_system_stats, get_top_processes

PROJECT_ROOT = Path("/app/project").resolve()
MODULES_DIR = Path("/app/modules").resolve()
INBOX_DIR = MODULES_DIR / "nyx" / "data" / "inbox"
EDITS_LOG_PATH = Path("/app/internal/edits_log.json")  # видна через /app/project, но в DENYLIST_NAMES

DENYLIST_NAMES = {".env", "edits_log.json", "restart_state.json", "deliberate_restart"}
DENYLIST_DIRS = {".git", "__pycache__", "node_modules", "caddy_data", "caddy_config", ".trash"}
TRASH_DIR = MODULES_DIR / ".trash"  # сюда переносятся удалённые модули — не безвозвратно

# Управляются самим хабом, не апдейтятся через apply_file_edit напрямую —
# писать в них вручную испортит формат, который читает core/memory.py.
# nyx/router.py и nyx/manifest.json — код чата, физически рядом с её данными,
# но защищён так же: единственный способ поменять — git, не через неё саму.
WRITE_DENYLIST_RELATIVE = {"nyx/data/current_topic.txt", "nyx/router.py", "nyx/manifest.json"}
WRITE_DENYLIST_PREFIXES = ("nyx/data/threads/", "nyx/data/inbox/")

# Влияют на монтирование конкретного модуля — правка триггерит hot-reload
# ЕГО процесса (не рестарт хаба). Остальные файлы применяются мгновенно.
RESTART_TRIGGER_NAMES = {"router.py", "manifest.json", "requirements.txt"}

REQUIRED_MANIFEST_KEYS = {"name", "version", "enabled", "status", "description"}

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Показать список файлов и папок проекта NEXUS404 по относительному пути от корня репозитория.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Относительный путь, например 'hub/core' или '.' для корня проекта",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Прочитать содержимое файла проекта NEXUS404 (только чтение).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Относительный путь к файлу от корня проекта, например 'modules/nyx/facts.json'",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_file_edit",
            "description": (
                "Создать НОВЫЙ файл или полностью ПЕРЕЗАПИСАТЬ существующий, где угодно внутри "
                "modules/. Путь вида '<модуль>/<файл>', например 'nyx/facts.json', "
                "'interface/app.js', 'notes/router.py'. Для НОВОГО файла — используй это. "
                "Для правки СУЩЕСТВУЮЩЕГО файла, если меняется не всё содержимое — используй "
                "patch_file, не это: patch_file трогает только нужный фрагмент, apply_file_edit "
                "заменяет файл целиком и рискует потерять то, что не собирались менять. "
                "Перед перезаписью router.py проверяется синтаксис, manifest.json — обязательные "
                "поля; если не проходит — файл не меняется, вернётся описание ошибки. "
                "Старая версия сохраняется в <файл>.bak — можно откатить через revert_file_edit. "
                "Правка router.py/manifest.json/requirements.txt поднимает/пересобирает ТОЛЬКО "
                "этот модуль за пару секунд, без рестарта хаба."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Путь относительно modules/, например 'nyx/prompt.md' или 'notes/router.py'",
                    },
                    "content": {
                        "type": "string",
                        "description": "Полное новое содержимое файла",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Короткое объяснение, что и зачем меняется",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": (
                "Точечная правка СУЩЕСТВУЮЩЕГО файла: заменяет old_str на new_str, не трогая "
                "остальное содержимое. ПРЕДПОЧТИТЕЛЬНЕЕ apply_file_edit для любой правки, которая "
                "не 'перепиши файл с нуля' — так исключён риск случайно потерять несвязанный код "
                "при 'перепечатывании' всего файла по памяти. old_str должен встречаться в файле "
                "РОВНО ОДИН РАЗ — если совпадений 0 или больше 1, вернётся ошибка без изменений "
                "(добавь больше окружающего контекста в old_str, чтобы совпадение стало уникальным). "
                "Перед вызовом сначала read_file — не полагайся на память о содержимом."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Путь относительно modules/, например 'nyx/prompt.md'",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "Точный фрагмент, который нужно заменить (должен встречаться ровно 1 раз)",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Чем заменить old_str",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Короткое объяснение, что и зачем меняется",
                    },
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revert_file_edit",
            "description": "Откатить последнее изменение конкретного файла в modules/ к версии из .bak.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь относительно modules/"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_path",
            "description": (
                "Удалить файл или папку модуля внутри modules/ — например, снести весь "
                "устаревший модуль целиком (передай 'notes', не 'notes/'), когда его недостаточно "
                "просто выключить через enabled:false (выключенный модуль всё равно остаётся "
                "виден в списке /api/modules — это по конвенции, а не баг: 'выключен' и 'удалён' "
                "разные вещи). apply_file_edit не умеет удалять, только создавать/перезаписывать — "
                "поэтому для полного удаления нужен именно этот инструмент. Не безвозвратно: "
                "перемещает в modules/.trash/, не стирает с диска физически. Папки nyx/ и "
                "interface/ трогать нельзя вообще — вернёт ошибку."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Путь относительно modules/, например 'notes' (весь модуль) или 'notes/old_file.py'",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_topic",
            "description": (
                "Переключить текущую тему разговора. Используй, когда Владимир ЯВНО предлагает "
                "начать что-то отдельное: 'давай напишем код', 'помоги мне с игрой', 'поговорим "
                "про Х' — это явный сигнал переключиться, не жди команды /тема от него. НЕ "
                "переключай на расплывчатый или мимолётный повод (один вопрос в сторону посреди "
                "другого разговора — не повод, см. правило про разовые вопросы). Переключение "
                "вступит в силу со СЛЕДУЮЩЕГО сообщения — то, что сказал Владимир прямо сейчас, "
                "останется в старой теме, а дальнейший разговор пойдёт уже в новой."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Название темы: буквы/цифры/_/- , до 30 символов, например 'код' или 'игра'",
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_inbox",
            "description": (
                "Показать файлы, которые Владимир загрузил в чат через кнопку прикрепления. "
                "Чтобы прочитать содержимое — read_file с путём 'modules/nyx/data/inbox/<имя>'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_stats",
            "description": "Показать текущую загрузку сервера: CPU, RAM, диск, сеть, аптайм. Только чтение, без shell.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_processes",
            "description": "Топ процессов сервера по нагрузке CPU (имя, pid, cpu%, ram%). Только чтение, без shell.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Сколько процессов показать (по умолчанию 10)"}
                },
                "required": [],
            },
        },
    },
]


def _resolve_readable(rel_path: str) -> Path | None:
    rel_path = (rel_path or ".").lstrip("/")
    candidate = (PROJECT_ROOT / rel_path).resolve()

    if candidate != PROJECT_ROOT and PROJECT_ROOT not in candidate.parents:
        return None
    if any(part in DENYLIST_DIRS or part in DENYLIST_NAMES for part in candidate.parts):
        return None
    return candidate


def _resolve_writable(rel_path: str) -> Path | None:
    rel_path = (rel_path or "").lstrip("/")
    if not rel_path:
        return None

    candidate = (MODULES_DIR / rel_path).resolve()
    if candidate != MODULES_DIR and MODULES_DIR not in candidate.parents:
        return None
    if any(part in DENYLIST_DIRS or part in DENYLIST_NAMES for part in candidate.parts):
        return None
    return candidate


def _is_write_denied(rel_path: str) -> bool:
    rel_path = rel_path.lstrip("/")
    if rel_path in WRITE_DENYLIST_RELATIVE:
        return True
    return any(rel_path.startswith(p) for p in WRITE_DENYLIST_PREFIXES)


def _lint_before_write(rel_path: str, content: str) -> str | None:
    """Возвращает текст ошибки, если правку нельзя применять как есть, иначе None."""
    name = Path(rel_path).name

    if name == "router.py":
        try:
            ast.parse(content)
        except SyntaxError as e:
            return f"router.py не проходит проверку синтаксиса: {e}"

    if name == "manifest.json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return f"manifest.json — невалидный JSON: {e}"
        missing = REQUIRED_MANIFEST_KEYS - data.keys()
        if missing:
            return f"manifest.json: не хватает обязательных полей: {sorted(missing)}"
        if not isinstance(data.get("enabled"), bool):
            return "manifest.json: поле 'enabled' должно быть true/false"

    return None


def list_files(path: str = ".") -> dict:
    target = _resolve_readable(path)
    if target is None or not target.is_dir():
        return {"error": "путь недоступен или это не директория"}

    entries = []
    for item in sorted(target.iterdir()):
        if item.name in DENYLIST_NAMES or item.name in DENYLIST_DIRS:
            continue
        entries.append(item.name + ("/" if item.is_dir() else ""))

    return {"path": path, "entries": entries}


def read_file(path: str, max_chars: int = 20000) -> dict:
    target = _resolve_readable(path)
    if target is None or not target.is_file():
        return {"error": "файл недоступен или не существует"}

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"error": f"не удалось прочитать: {e}"}

    return {
        "path": path,
        "content": content[:max_chars],
        "truncated": len(content) > max_chars,
    }


def _load_log() -> list:
    if not EDITS_LOG_PATH.exists():
        return []
    try:
        return json.loads(EDITS_LOG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_log(items: list) -> None:
    EDITS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    EDITS_LOG_PATH.write_text(json.dumps(items[-200:], ensure_ascii=False, indent=2), encoding="utf-8")


def _maybe_hot_reload(path: str) -> dict:
    """Если правка задела router.py/manifest.json/requirements.txt пользовательского
    модуля — поднимает/пересобирает ТОЛЬКО его subprocess, хаб не трогается.
    nyx/interface сюда попасть не могут: nyx денилистом, interface — не router."""
    filename = Path(path).name
    if filename not in RESTART_TRIGGER_NAMES:
        return {"hot_reloaded": False}

    parts = Path(path).parts
    if not parts or parts[0] in ("nyx", "interface"):
        return {"hot_reloaded": False}

    module_name = parts[0]
    module_dir = MODULES_DIR / module_name

    if filename == "requirements.txt":
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", str(module_dir / "requirements.txt")],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return {"hot_reloaded": False, "pip_error": result.stderr[-500:]}

    if not (module_dir / "router.py").exists():
        # Например, записан только manifest.json, router.py ещё не написан —
        # подождём, пока появится, поднимать нечего.
        return {"hot_reloaded": False}

    manifest_path = module_dir / "manifest.json"
    enabled = True
    if manifest_path.exists():
        manifest = load_manifest_safely(manifest_path)
        enabled = manifest.get("enabled", True) if manifest else True

    from core.module_supervisor import reload_or_spawn_module
    outcome = reload_or_spawn_module(module_name, module_dir, enabled=enabled)
    return {"hot_reloaded": True, "module_healthy": outcome["healthy"], "module_new": outcome["is_new"]}


def load_manifest_safely(manifest_path: Path) -> dict | None:
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_and_track(path: str, target: Path, content: str, reason: str) -> dict:
    """Общая часть apply_file_edit/patch_file: бэкап, запись, лог, hot-reload.
    Раздельная валидация (денилист/лint/уникальность old_str) — в вызывающей функции."""
    if target.exists():
        backup_path = target.with_name(target.name + ".bak")
        shutil.copy2(target, backup_path)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    log = _load_log()
    log.append({
        "id": uuid.uuid4().hex[:8],
        "path": path,
        "reason": reason,
        "at": datetime.now().isoformat(),
    })
    _save_log(log)

    reload_result = _maybe_hot_reload(path)
    return {"applied": True, "path": path, **reload_result}


def apply_file_edit(path: str, content: str, reason: str = "") -> dict:
    if _is_write_denied(path):
        return {"error": f"'{path}' управляется хабом напрямую, apply_file_edit сюда не пишет"}

    target = _resolve_writable(path)
    if target is None:
        return {"error": "путь должен быть внутри modules/ и не задевать системные файлы"}

    lint_error = _lint_before_write(path, content)
    if lint_error:
        return {"error": lint_error, "applied": False}

    return _write_and_track(path, target, content, reason)


def patch_file(path: str, old_str: str, new_str: str, reason: str = "") -> dict:
    """Точечная правка: заменяет old_str на new_str, old_str обязан встречаться
    ровно один раз. В отличие от apply_file_edit не требует держать в голове
    весь файл — трогает только совпавший фрагмент, остальное гарантированно
    не меняется (не полагается на аккуратность 'перепечатывания')."""
    if _is_write_denied(path):
        return {"error": f"'{path}' управляется хабом напрямую, patch_file сюда не пишет"}

    target = _resolve_writable(path)
    if target is None:
        return {"error": "путь должен быть внутри modules/ и не задевать системные файлы"}
    if not target.exists():
        return {"error": "файл не существует — для нового файла используй apply_file_edit"}

    try:
        current = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"error": "файл не в UTF-8 — patch_file не может его безопасно прочитать"}

    count = current.count(old_str)
    if count == 0:
        return {"error": "old_str не найден в файле — файл мог измениться, сначала read_file заново"}
    if count > 1:
        return {"error": f"old_str встречается {count} раз(а), нужен ровно 1 — добавь больше окружающего контекста"}

    new_content = current.replace(old_str, new_str)

    lint_error = _lint_before_write(path, new_content)
    if lint_error:
        return {"error": lint_error, "applied": False}

    return _write_and_track(path, target, new_content, reason)


def delete_path(path: str) -> dict:
    """Удаляет файл/папку модуля — переносом в modules/.trash/, не безвозвратно.
    nyx/ и interface/ трогать нельзя ни целиком, ни частично: это не про
    доверие, просто у apply_file_edit нет и не должно быть способа снести
    protected-код или интерфейс хаба, а delete_path — тот же периметр записи."""
    path = (path or "").strip("/")
    if not path:
        return {"error": "путь не может быть пустым"}

    parts = Path(path).parts
    if not parts:
        return {"error": "некорректный путь"}
    if parts[0] in ("nyx", "interface"):
        return {"error": f"'{parts[0]}' защищена — удалять оттуда нельзя, только через git"}

    target = _resolve_writable(path)
    if target is None or not target.exists():
        return {"error": "путь не найден или вне modules/"}

    module_name = parts[0]

    # Удаляем весь модуль или его код — сначала гасим subprocess, иначе он
    # повиснет, ссылаясь на уже унесённые в корзину файлы.
    if len(parts) == 1 or (len(parts) > 1 and parts[1] in ("router.py", "manifest.json")):
        from core.module_supervisor import stop_module_process
        stop_module_process(module_name)

    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    trash_name = f"{'_'.join(parts)}-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    dest = TRASH_DIR / trash_name
    shutil.move(str(target), str(dest))

    log = _load_log()
    log.append({
        "id": uuid.uuid4().hex[:8],
        "path": f"[удалено] {path}",
        "reason": f"перенесено в modules/.trash/{trash_name}",
        "at": datetime.now().isoformat(),
    })
    _save_log(log)

    return {"deleted": True, "path": path, "trash": f".trash/{trash_name}"}


def revert_file_edit(path: str) -> dict:
    target = _resolve_writable(path)
    if target is None:
        return {"error": "путь должен быть внутри modules/"}

    backup_path = target.with_name(target.name + ".bak")
    if not backup_path.exists():
        return {"error": "нет резервной копии для этого файла"}

    swap_path = target.with_name(target.name + ".tmp_swap")
    if target.exists():
        shutil.copy2(target, swap_path)
    shutil.copy2(backup_path, target)
    if swap_path.exists():
        shutil.move(str(swap_path), str(backup_path))

    reload_result = _maybe_hot_reload(path)

    return {"reverted": True, "path": path, **reload_result}


def list_recent_edits(limit: int = 10) -> list:
    return _load_log()[-limit:][::-1]


def list_inbox() -> dict:
    if not INBOX_DIR.exists():
        return {"files": []}
    return {"files": sorted(p.name for p in INBOX_DIR.iterdir() if p.is_file() and p.name != ".gitkeep")}


def switch_topic(topic: str) -> dict:
    from core.memory import is_valid_topic, set_current_topic
    topic = (topic or "").strip().lower()
    if not is_valid_topic(topic):
        return {"error": "название темы: буквы, цифры, _ и -, до 30 символов"}
    set_current_topic(topic)
    return {"switched": True, "topic": topic, "note": "вступит в силу со следующего сообщения"}


TOOL_FUNCTIONS = {
    "list_files": lambda args: list_files(args.get("path", ".")),
    "read_file": lambda args: read_file(args.get("path", "")),
    "apply_file_edit": lambda args: apply_file_edit(
        args.get("path", ""), args.get("content", ""), args.get("reason", "")
    ),
    "patch_file": lambda args: patch_file(
        args.get("path", ""), args.get("old_str", ""), args.get("new_str", ""), args.get("reason", "")
    ),
    "revert_file_edit": lambda args: revert_file_edit(args.get("path", "")),
    "delete_path": lambda args: delete_path(args.get("path", "")),
    "list_inbox": lambda args: list_inbox(),
    "switch_topic": lambda args: switch_topic(args.get("topic", "")),
    "get_system_stats": lambda args: get_system_stats(),
    "get_top_processes": lambda args: get_top_processes(args.get("limit", 10)),
}
