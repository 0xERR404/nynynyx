"""
Один subprocess на модуль (см. module_runner.py), хаб — reverse-proxy к нему
по /api/<имя>/... через httpx. Зависший/упавший модуль убивает только свой
процесс — хаб и чат с Никс продолжают отвечать; при неудачном старте
health-check не проходит, эндпоинты отдают 502 вместо падения хаба.
"""

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from fastapi import Request, Response

logger = logging.getLogger("nexus404.hub")

BASE_PORT = 8100
HEALTH_TIMEOUT = 5.0        # сколько ждём при старте, чтобы процесс поднялся
RECHECK_INTERVAL = 20.0     # как часто фоновый поток проверяет живость процессов
MAX_RESTARTS_PER_MODULE = 5  # после стольких падений подряд — прекращаем попытки, ждём ручной правки


class ManagedModule:
    def __init__(self, name: str, path: Path, port: int):
        self.name = name
        self.path = path
        self.port = port
        self.process: subprocess.Popen | None = None
        self.restart_count = 0
        self.healthy = False

    def start(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "core.module_runner", str(self.path), str(self.port)],
            cwd="/app",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.healthy = self._wait_healthy()
        if not self.healthy:
            stderr = ""
            if self.process.poll() is not None and self.process.stderr:
                stderr = self.process.stderr.read().decode(errors="replace")[-1000:]
            logger.error(f"[modules] {self.name}: не поднялся на порту {self.port}. {stderr}")

    def _wait_healthy(self) -> bool:
        deadline = time.time() + HEALTH_TIMEOUT
        while time.time() < deadline:
            if self.process.poll() is not None:
                return False  # процесс уже умер — ждать нет смысла
            try:
                r = httpx.get(f"http://127.0.0.1:{self.port}/__health", timeout=0.5)
                if r.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        return False

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


_modules: dict[str, ManagedModule] = {}
_lock = threading.Lock()
_on_new_module = None  # main.py подставит сюда функцию регистрации прокси-роута


def list_module_names() -> list[str]:
    return list(_modules.keys())


def set_new_module_callback(fn) -> None:
    """main.py вызывает это один раз при старте — так reload_or_spawn_module
    может зарегистрировать прокси-роут для НОВОГО модуля, не импортируя main.py
    напрямую (циклический импорт: main.py уже импортирует этот файл)."""
    global _on_new_module
    _on_new_module = fn


def spawn_module(name: str, path: Path) -> bool:
    with _lock:
        port = BASE_PORT + len(_modules)
        m = ManagedModule(name, path, port)
        m.start()
        _modules[name] = m
        if m.healthy:
            logger.info(f"[modules] подключён (изолированно): {name} :{port}")
        return m.healthy


def stop_module_process(name: str) -> None:
    """Останавливает subprocess модуля, если он запущен. Вызывается перед
    физическим удалением его файлов (delete_path в core/tools.py) — иначе
    процесс продолжит висеть, ссылаясь на уже стёртые файлы."""
    with _lock:
        m = _modules.pop(name, None)
        if m and m.process and m.is_alive():
            m.process.terminate()
            try:
                m.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                m.process.kill()
            logger.info(f"[modules] {name}: процесс остановлен (удаление)")


def reload_or_spawn_module(name: str, path: Path, enabled: bool = True) -> dict:
    """Поднимает/пересобирает ОДИН модуль без рестарта хаба. Вызывается сразу
    после apply_file_edit на router.py/manifest.json/requirements.txt —
    новый или изменённый модуль появляется на лету, хаб и чат не прерываются."""
    with _lock:
        old = _modules.get(name)
        is_new = old is None

        if old is not None and old.process is not None and old.is_alive():
            old.process.terminate()
            try:
                old.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                old.process.kill()

        if not enabled:
            _modules.pop(name, None)
            logger.info(f"[modules] {name}: выключен в manifest.json, процесс остановлен")
            return {"healthy": False, "is_new": is_new, "stopped": True}

        port = old.port if old is not None else BASE_PORT + len(_modules)
        m = ManagedModule(name, path, port)
        m.start()
        _modules[name] = m

        if m.healthy:
            logger.info(f"[modules] {'подключён' if is_new else 'обновлён'} без рестарта хаба: {name} :{port}")

    if is_new and m.healthy and _on_new_module:
        _on_new_module(name)

    return {"healthy": m.healthy, "is_new": is_new, "port": port}


def _restart_if_needed() -> None:
    """Если модуль умер сам — пробуем поднять заново, но с потолком попыток,
    иначе сломанный модуль тихо жрёт CPU циклом падений."""
    with _lock:
        for m in list(_modules.values()):
            if m.is_alive():
                continue
            if m.restart_count >= MAX_RESTARTS_PER_MODULE:
                continue
            m.restart_count += 1
            logger.warning(f"[modules] {m.name}: процесс умер, попытка перезапуска {m.restart_count}/{MAX_RESTARTS_PER_MODULE}")
            m.start()


def start_watchdog() -> None:
    def loop():
        while True:
            time.sleep(RECHECK_INTERVAL)
            _restart_if_needed()
    threading.Thread(target=loop, daemon=True).start()


async def proxy_to_module(name: str, rest_path: str, request: Request) -> Response:
    m = _modules.get(name)
    if m is None or not m.is_alive() or not m.healthy:
        return Response(
            content=f'{{"error": "модуль {name} не запущен (упал при старте, не прошёл health-check, или сейчас не отвечает)"}}',
            status_code=502, media_type="application/json",
        )

    url = f"http://127.0.0.1:{m.port}/{rest_path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.request(
                request.method, url, headers=headers, content=body, params=request.query_params,
            )
    except httpx.HTTPError as e:
        return Response(
            content=f'{{"error": "модуль {name} не ответил: {e}"}}',
            status_code=502, media_type="application/json",
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
