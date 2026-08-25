from pathlib import Path

from fastapi import APIRouter

from core.manifest import load_manifest

BASE_DIR = Path(__file__).resolve().parent.parent
MODULES_DIR = BASE_DIR / "modules"
RESERVED_MODULE_NAMES = {"nyx", "interface"}

router = APIRouter()


def _scan(directory: Path, skip_reserved: bool = False) -> list:
    result = []
    if not directory.exists():
        return result

    for module_dir in sorted(directory.iterdir()):
        if not module_dir.is_dir() or module_dir.name.startswith(("_", ".")):
            continue
        if skip_reserved and module_dir.name in RESERVED_MODULE_NAMES:
            continue

        manifest = load_manifest(module_dir)
        if manifest is None:
            if (module_dir / "manifest.json").exists():
                result.append({
                    "folder": module_dir.name,
                    "name": module_dir.name,
                    "status": "broken (invalid manifest.json)",
                    "enabled": False,
                    "builtin": False,
                })
            continue

        result.append({
            "folder": module_dir.name,
            "name": manifest.get("name", module_dir.name),
            "version": manifest.get("version", "0.0.0"),
            "status": manifest.get("status", "unknown"),
            "enabled": manifest.get("enabled", False),
            "description": manifest.get("description", ""),
            "builtin": False,
        })

    return result


def list_modules() -> list:
    result = []

    # nyx — тоже modules/nyx/, просто router.py/manifest.json защищены
    # write-денилистом (core/tools.py), а не отдельной директорией.
    nyx_manifest = load_manifest(MODULES_DIR / "nyx")
    if nyx_manifest:
        result.append({
            "folder": "nyx",
            "name": nyx_manifest.get("name", "Nyx"),
            "version": nyx_manifest.get("version", "0.0.0"),
            "status": nyx_manifest.get("status", "unknown"),
            "enabled": nyx_manifest.get("enabled", False),
            "description": nyx_manifest.get("description", ""),
            "builtin": True,
        })

    # interface — статика, не router-модуль, поэтому не в списке вообще
    result += _scan(MODULES_DIR, skip_reserved=True)
    return result


@router.get("/modules")
async def get_modules():
    return {"modules": list_modules()}
