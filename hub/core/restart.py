"""
Self-restart без docker-сокета: хаб завершает свой процесс, `restart:
unless-stopped` в docker-compose поднимает контейнер заново.

Crash-loop защита: если падений подряд слишком много за короткое время —
следующий старт не монтирует modules/, только её чат и аварийный интерфейс,
чтобы можно было откатить правку даже при серьёзной поломке.

Важно: рестарт после её же успешной правки (router.py/manifest.json) —
ОЖИДАЕМЫЙ, не падение. Если она пишет несколько модулей подряд, это
несколько намеренных рестартов за пару минут — их не нужно путать с
реальным crash-loop. Помечаем такие рестарты файлом-маркером перед выходом;
при следующем старте видим маркер — счётчик не растёт.
"""

import json
import os
import threading
import time
from pathlib import Path

RESTART_STATE_PATH = Path("/app/internal/restart_state.json")
DELIBERATE_MARKER_PATH = Path("/app/internal/deliberate_restart")
CRASH_WINDOW_SECONDS = 120   # рестарты внутри этого окна считаем подряд идущими
CRASH_THRESHOLD = 4          # столько НЕЗАПЛАНИРОВАННЫХ рестартов подряд — включаем safe mode


def schedule_self_restart(delay: float = 1.5) -> None:
    def _restart():
        DELIBERATE_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        DELIBERATE_MARKER_PATH.touch()
        os._exit(0)
    threading.Timer(delay, _restart).start()


def _load_state() -> dict:
    if not RESTART_STATE_PATH.exists():
        return {"count": 0, "last_at": 0}
    try:
        return json.loads(RESTART_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"count": 0, "last_at": 0}


def _save_state(state: dict) -> None:
    RESTART_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESTART_STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def register_start_and_check_safe_mode() -> bool:
    """Вызывать один раз при старте, до монтирования modules/. True = safe mode."""
    if DELIBERATE_MARKER_PATH.exists():
        # Это она сама попросила перезапуститься после удачной правки —
        # не падение, счётчик не трогаем вообще.
        DELIBERATE_MARKER_PATH.unlink()
        return False

    now = time.time()
    state = _load_state()

    if now - state.get("last_at", 0) <= CRASH_WINDOW_SECONDS:
        state["count"] = state.get("count", 0) + 1
    else:
        state["count"] = 1
    state["last_at"] = now
    _save_state(state)

    return state["count"] >= CRASH_THRESHOLD


def reset_crash_counter() -> None:
    """Вызывать после стабильной работы — иначе старый всплеск падений держит safe mode вечно."""
    _save_state({"count": 0, "last_at": 0})
