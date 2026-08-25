import importlib.util
import json
from pathlib import Path


def load_manifest(module_dir: Path) -> dict | None:
    """Читает manifest.json модуля. Возвращает None, если файла нет или он битый —
    вызывающий код сам решает, как это отобразить (пропустить / показать broken)."""
    manifest_path = module_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_enabled_manifest(module_dir: Path) -> tuple[dict | None, str | None]:
    """Общая проверка перед монтированием модуля (что для nyx, что для
    пользовательских): есть ли файлы, валиден ли manifest.json, включён ли модуль.
    Возвращает (manifest, None) если всё ок, иначе (None, причина) — вызывающий
    код сам решает, как залогировать конкретную причину."""
    manifest_path = module_dir / "manifest.json"
    router_path = module_dir / "router.py"
    if not manifest_path.exists() or not router_path.exists():
        return None, None  # не модуль вообще — не ошибка, просто пропускаем молча

    manifest = load_manifest(module_dir)
    if manifest is None:
        return None, "invalid_manifest"
    if not manifest.get("enabled", False):
        return None, "disabled"
    return manifest, None


def load_router_module(router_path: Path, namespace: str):
    """Динамически импортирует router.py как модуль Python. Общий код для
    main.py::mount_nyx (общий процесс) и module_runner.py (пользовательские
    модули, отдельный процесс) — раньше был продублирован почти дословно."""
    spec = importlib.util.spec_from_file_location(namespace, router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
