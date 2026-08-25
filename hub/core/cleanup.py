"""
Файлы в inbox (загруженное) и outbox (сгенерированное — картинки, треки,
отчёты) не должны копиться вечно. Раз в CHECK_INTERVAL проверяем возраст
по mtime и удаляем то, что старше RETENTION_SECONDS.
"""

import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger("nexus404.hub")

RETENTION_SECONDS = 30 * 24 * 3600   # месяц
CHECK_INTERVAL = 6 * 3600            # проверка раз в 6 часов


def _purge_old(directory: Path) -> None:
    if not directory.exists():
        return
    cutoff = time.time() - RETENTION_SECONDS
    for f in directory.iterdir():
        if f.is_file() and f.name != ".gitkeep" and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                logger.info(f"[cleanup] удалён протухший файл: {f}")
            except OSError as e:
                logger.warning(f"[cleanup] не удалось удалить {f}: {e}")


def start_cleanup_watchdog(dirs: list[Path]) -> None:
    def loop():
        while True:
            for d in dirs:
                _purge_old(d)
            time.sleep(CHECK_INTERVAL)
    threading.Thread(target=loop, daemon=True).start()
