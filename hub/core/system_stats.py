"""
Read-only метрики сервера. Никакого shell и никакого docker-сокета —
только psutil читает /proc напрямую внутри контейнера.

Сознательно НЕ включено: интроспекция Docker (список контейнеров, их
статус и т.п.) — это потребовало бы примонтировать docker.sock, а сам
сокет — это уже не "только чтение": получив к нему доступ, можно и
писать (создавать/останавливать контейнеры). Это ровно тот шаг, который
мы отдельно обсуждали и сознательно не делаем.

Также не отдаём полный cmdline() процессов — в аргументах командной
строки иногда передают секреты, лучше не рисковать даже для read-only.
"""

import time

import psutil

_BOOT_TIME = psutil.boot_time()
psutil.cpu_percent(interval=None)  # прогрев счётчика, иначе первый вызов вернёт 0.0


def get_system_stats() -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    disk_io = psutil.disk_io_counters()
    net_io = psutil.net_io_counters()

    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_cores": psutil.cpu_count(logical=True),
        "load_avg": list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else None,
        "ram_percent": vm.percent,
        "ram_used_gb": round(vm.used / (1024 ** 3), 2),
        "ram_total_gb": round(vm.total / (1024 ** 3), 2),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / (1024 ** 3), 2),
        "disk_total_gb": round(disk.total / (1024 ** 3), 2),
        "disk_read_mb": round(disk_io.read_bytes / (1024 ** 2), 1) if disk_io else None,
        "disk_write_mb": round(disk_io.write_bytes / (1024 ** 2), 1) if disk_io else None,
        "net_sent_mb": round(net_io.bytes_sent / (1024 ** 2), 1) if net_io else None,
        "net_recv_mb": round(net_io.bytes_recv / (1024 ** 2), 1) if net_io else None,
        "uptime_seconds": int(time.time() - _BOOT_TIME),
    }


def get_top_processes(limit: int = 10) -> list:
    """Топ процессов по CPU. Без cmdline/environ — только имя, pid, потребление ресурсов."""
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            procs.append({
                "pid": info["pid"],
                "name": info["name"],
                "cpu_percent": round(info["cpu_percent"] or 0.0, 1),
                "memory_percent": round(info["memory_percent"] or 0.0, 1),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x["cpu_percent"], reverse=True)
    return procs[:limit]
