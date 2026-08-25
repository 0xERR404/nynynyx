"""
Отдельный процесс на один модуль — если он упадёт/зависнет, умирает только
он, не хаб. Запуск: python -m core.module_runner <путь_к_модулю> <порт>.
Импорт router.py — через core.manifest.load_router_module (общий код с main.py).
"""

import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from core.manifest import load_router_module


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python -m core.module_runner <module_dir> <port>", file=sys.stderr)
        sys.exit(1)

    module_dir = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2])
    router_path = module_dir / "router.py"

    mod = load_router_module(router_path, f"isolated_{module_dir.name}_router")

    if not hasattr(mod, "router"):
        print(f"{module_dir.name}: router.py не содержит объект `router`", file=sys.stderr)
        sys.exit(1)

    app = FastAPI(title=f"NEXUS404 module: {module_dir.name}")
    app.include_router(mod.router)

    @app.get("/__health")
    async def health():
        return {"ok": True}

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
