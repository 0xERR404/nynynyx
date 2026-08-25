import json
import re
from pathlib import Path

# Её содержимое (identity/факты/промпт/история) — modules/nyx/, пишет сама через apply_file_edit
NYX_DIR = Path("/app/modules/nyx")
MEMORY_PATH = NYX_DIR / "memory.json"
FACTS_PATH = NYX_DIR / "facts.json"
REPLIES_PATH = NYX_DIR / "replies.json"
THREADS_DIR = NYX_DIR / "data" / "threads"
CURRENT_TOPIC_PATH = NYX_DIR / "data" / "current_topic.txt"

DEFAULT_TOPIC = "общее"
HARD_CAP_MESSAGES = 200  # абсолютный предохранитель на случай, если сжатие вообще не сработает
TOPIC_PATTERN = re.compile(r"^[a-zA-Zа-яА-ЯёЁ0-9_-]{1,30}$")


def load_memory() -> dict:
    """Перечитываем при каждом обращении — правки в memory.json подхватываются
    без перезапуска контейнера."""
    with open(MEMORY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_replies() -> dict:
    with open(REPLIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_facts(topic: str = None) -> list:
    """Факты, относящиеся к теме разговора + всегда 'общее' — не тащим в
    контекст факты из чужих тем, экономим токены."""
    if not FACTS_PATH.exists():
        return []
    try:
        all_facts = json.loads(FACTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    topic = topic or get_current_topic()
    return [f for f in all_facts if f.get("topic") in (topic, "общее")]


def is_valid_topic(name: str) -> bool:
    return bool(TOPIC_PATTERN.match(name or ""))


def _thread_path(topic: str) -> Path:
    return THREADS_DIR / f"{topic}.json"


def get_current_topic() -> str:
    if not CURRENT_TOPIC_PATH.exists():
        return DEFAULT_TOPIC
    topic = CURRENT_TOPIC_PATH.read_text(encoding="utf-8").strip()
    return topic if is_valid_topic(topic) else DEFAULT_TOPIC


def set_current_topic(topic: str) -> bool:
    if not is_valid_topic(topic):
        return False
    CURRENT_TOPIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_TOPIC_PATH.write_text(topic, encoding="utf-8")
    return True


def load_history(topic: str = None) -> list:
    topic = topic or get_current_topic()
    path = _thread_path(topic)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []


def save_history(history: list, topic: str = None) -> None:
    topic = topic or get_current_topic()
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_thread_path(topic), "w", encoding="utf-8") as f:
        json.dump(history[-HARD_CAP_MESSAGES:], f, ensure_ascii=False, indent=2)


def clear_history(topic: str = None) -> None:
    save_history([], topic)
    save_summary("", topic)


def _summary_path(topic: str) -> Path:
    return THREADS_DIR / f"{topic}_summary.txt"


def load_summary(topic: str = None) -> str:
    topic = topic or get_current_topic()
    path = _summary_path(topic)
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def save_summary(text: str, topic: str = None) -> None:
    topic = topic or get_current_topic()
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    _summary_path(topic).write_text(text, encoding="utf-8")


def list_topics() -> list:
    if not THREADS_DIR.exists():
        return []
    current = get_current_topic()
    result = []
    for path in sorted(THREADS_DIR.glob("*.json")):
        topic = path.stem
        try:
            count = len(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            count = 0
        result.append({"topic": topic, "messages": count, "current": topic == current})
    return result
