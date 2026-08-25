"""
Единый аудит-лог: каждый вызов инструмента DeepSeek (чтение, запись,
метрики — всё) пишется сюда с меткой времени. Это чисто наблюдаемость,
прав это никому не добавляет.

Полное содержимое файлов сюда НЕ льётся — длинные строковые поля (например
"content" при apply_file_edit/read_file) заменяются на "<N chars>", иначе
лог быстро распухнет до гигабайт и станет бесполезен для чтения.
"""

import json
import os
from datetime import datetime
from pathlib import Path

AUDIT_LOG_PATH = Path("/app/shared/logs/audit.log")
_MAX_FIELD_CHARS = 200
_MAX_LOG_BYTES = 5 * 1024 * 1024  # ротация в 5МБ — не даём файлу расти бесконечно

_SECRET = os.getenv("DEEPSEEK_API_KEY", "")


def _mask_secrets(text: str) -> str:
    if _SECRET and _SECRET in text:
        text = text.replace(_SECRET, "***")
    return text


def _sanitize(value):
    if isinstance(value, str):
        value = _mask_secrets(value)
        if len(value) > _MAX_FIELD_CHARS:
            return f"<{len(value)} chars>"
        return value
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value[:20]]
    return value


def _rotate_if_needed() -> None:
    try:
        if AUDIT_LOG_PATH.exists() and AUDIT_LOG_PATH.stat().st_size > _MAX_LOG_BYTES:
            AUDIT_LOG_PATH.rename(AUDIT_LOG_PATH.with_suffix(".log.1"))
    except Exception:
        pass


def log_tool_call(name: str, args: dict, result) -> None:
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed()
        entry = {
            "at": datetime.now().isoformat(),
            "tool": name,
            "args": _sanitize(args or {}),
            "result": _sanitize(result if isinstance(result, dict) else {"value": result}),
        }
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # аудит не должен ронять сам чат, если вдруг не смог записать
