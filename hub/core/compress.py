"""
Сжатие истории темы: старые "сырые" сообщения сжимаются в сводку отдельным
вызовом DeepSeek, а не выбрасываются. Если сжатие не удалось — историю не
обрезаем, лучше временно потратить больше токенов, чем потерять переписку.

Автоматическое накопление личных фактов (о Владимире/о ней) сюда сознательно
НЕ входит — по договорённости она не ведёт фоновую память о личном, только
знает, что она Никс. Если что-то из окружения нужно узнать (домен, структура
проекта) — читает файлы сама, не хранит как "факт".
"""

import logging

from core.llm import summarize_messages
from core.memory import load_history, load_summary, save_history, save_summary

logger = logging.getLogger("nexus404.hub")

# Порог — по ОБЪЁМУ ТЕКСТА, не по числу сообщений. Одно длинное сообщение
# (например, подробный ответ не по теме) весит для расхода токенов куда
# больше, чем десяток коротких ("[файл: x.py]"), но при счёте "по штукам"
# оба считались одинаково — тяжёлый ответ мог провисеть в истории до
# следующего сжатия дольше, чем стоило бы. Оценка "символы ≈ токены/2.5" —
# грубая, но достаточная, чтобы триггер реагировал на реальный вес, а не
# на количество реплик.
RAW_KEEP_CHARS = 6000        # сколько последних символов истории держим текстом
COMPRESS_TRIGGER_CHARS = 10000  # с какого объёма начинаем сжимать хвост
HARD_MESSAGE_FLOOR = 6       # не сжимаем, если сырых сообщений и так мало — сжатие тут не окупается


def _total_chars(history: list) -> int:
    return sum(len(m.get("content", "")) for m in history)


def _split_by_char_budget(history: list, keep_chars: int) -> tuple[list, list]:
    """Возвращает (overflow, remaining) — remaining набирается с конца, пока
    не наберёт keep_chars символов (целыми сообщениями, не обрезая на середине)."""
    remaining = []
    used = 0
    idx = len(history)
    for i in range(len(history) - 1, -1, -1):
        size = len(history[i].get("content", ""))
        if remaining and used + size > keep_chars:
            idx = i + 1
            break
        remaining.append(history[i])
        used += size
        idx = i
    remaining.reverse()
    return history[:idx], history[idx:]


async def maybe_compress(topic: str) -> None:
    history = load_history(topic)
    if len(history) <= HARD_MESSAGE_FLOOR:
        return
    if _total_chars(history) <= COMPRESS_TRIGGER_CHARS:
        return

    overflow, remaining = _split_by_char_budget(history, RAW_KEEP_CHARS)
    if not overflow:
        return
    old_summary = load_summary(topic)

    try:
        new_summary = await summarize_messages(old_summary, overflow)
    except Exception as e:
        logger.warning(f"[compress] тема «{topic}»: сжать не удалось ({e}), история не обрезана")
        return

    save_summary(new_summary, topic)
    save_history(remaining, topic)
    logger.info(
        f"[compress] тема «{topic}»: {len(overflow)} сообщ. ({_total_chars(overflow)} симв.) сжаты "
        f"в сводку ({len(new_summary)} симв.), оставлено {len(remaining)} сырых сообщений"
    )
