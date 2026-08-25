from datetime import datetime

from core.memory import (
    clear_history, get_current_topic, list_topics, load_history,
    load_replies, load_summary, set_current_topic,
)
from core.tools import list_recent_edits, revert_file_edit


def handle_command(command: str):
    """Возвращает готовый ответ, если сообщение — внутренняя команда, иначе None.
    Сами тексты ответов — в modules/nyx/replies.json, здесь только маршрутизация."""
    raw = command.strip()
    cmd = raw.lower()
    r = load_replies()

    if cmd == "/тишина":
        return r["тишина"]

    if cmd == "/итоги":
        return r["итоги"].format(date=datetime.now().strftime("%d.%m.%Y"))

    if cmd == "/статус":
        return r["статус"]

    if cmd == "/сброс":
        clear_history()
        return r["сброс"]

    if cmd == "/тема":
        return r["тема_текущая"].format(topic=get_current_topic())

    if cmd.startswith("/тема "):
        new_topic = raw.split(maxsplit=1)[1].strip().lower()
        if not set_current_topic(new_topic):
            return r["тема_неверное_имя"]
        has_history = bool(load_history(new_topic))
        note = r["тема_история_есть"] if has_history else r["тема_история_нет"]
        return r["тема_переключено"].format(topic=new_topic, history_note=note)

    if cmd == "/темы":
        topics = list_topics()
        if not topics:
            return r["темы_пусто"]
        lines = "\n".join(
            f"- {t['topic']} ({t['messages']} сообщ.){' ← текущая' if t['current'] else ''}"
            for t in topics
        )
        return r["темы_заголовок"].format(lines=lines)

    if cmd == "/сводка":
        summary = load_summary()
        if not summary:
            return r["сводка_пусто"].format(topic=get_current_topic())
        return r["сводка_есть"].format(topic=get_current_topic(), summary=summary)

    if cmd == "/история":
        edits = list_recent_edits()
        if not edits:
            return r["история_пусто"]
        lines = "\n".join(
            f"- {e['at'][:16].replace('T', ' ')} {e['path']} — {e.get('reason') or 'без описания'}"
            for e in edits
        )
        return r["история_заголовок"].format(lines=lines)

    if cmd.startswith("/откат "):
        parts = raw.split(maxsplit=1)
        if len(parts) < 2:
            return r["откат_формат"]
        path = parts[1].strip()
        result = revert_file_edit(path)
        if "error" in result:
            return r["откат_ошибка"].format(error=result["error"])
        if result["path"].startswith("interface/"):
            return r["откат_готово_interface"].format(path=result["path"])
        return r["откат_готово"].format(path=result["path"])

    return None
