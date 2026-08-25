from pathlib import Path

from core.memory import load_facts
from core.system_stats import get_system_stats

PROMPT_PATH = Path("/app/modules/nyx/prompt.md")


CPU_ALERT_PERCENT = 85
RAM_ALERT_PERCENT = 85
DISK_ALERT_PERCENT = 90
RECENT_RESTART_SECONDS = 300  # 5 минут — недавно перезапускались, стоит знать


def _vitals_line() -> str:
    """Пульс сервера — В ПРОМПТЕ ТОЛЬКО КОГДА ЧТО-ТО НЕ В НОРМЕ. В обычном
    состоянии — пустая строка: чату не нужны эти цифры для работы, это была
    фича "чувствует сервер как своё тело", но платить за неё токенами на
    КАЖДОМ сообщении, когда 99% времени всё в порядке, бессмысленно. Раз
    появился — значит реально стоит упомянуть, а не просто фоновый шум."""
    try:
        s = get_system_stats()
    except Exception:
        return ""  # не посчитали — не критично, молчим, не шумим зря

    anomalies = []
    if s["cpu_percent"] > CPU_ALERT_PERCENT:
        anomalies.append(f"CPU {s['cpu_percent']:.0f}%")
    if s["ram_percent"] > RAM_ALERT_PERCENT:
        anomalies.append(f"RAM {s['ram_percent']:.0f}%")
    if s["disk_percent"] > DISK_ALERT_PERCENT:
        anomalies.append(f"диск {s['disk_percent']:.0f}%")
    if s["uptime_seconds"] < RECENT_RESTART_SECONDS:
        anomalies.append(f"аптайм всего {s['uptime_seconds'] // 60} мин — недавно перезапускались")

    if not anomalies:
        return ""

    return "\n[Пульс сервера тревожный: " + ", ".join(anomalies) + ". Упомяни сама, без вопроса.]"


MAX_FACTS_IN_PROMPT = 8  # потолок — без него список фактов может расти в промпте бесконечно


def _facts_block(topic: str) -> str:
    facts = load_facts(topic)
    if not facts:
        return ""
    facts = sorted(facts, key=lambda f: -f.get("weight", 1))[:MAX_FACTS_IN_PROMPT]
    lines = [f"- ({f.get('kind', 'факт')}) {f['note']}" for f in facts]
    return "\nФакты по текущей теме и общие:\n" + "\n".join(lines) + "\n"


def build_system_prompt(memory: dict, topic: str = None) -> str:
    """Читает prompt.md и подставляет живые значения — только склейка, без текста персонажа.

    Пульс сервера (vitals) — В КОНЦЕ, не в начале. Он меняется почти на каждом
    сообщении (CPU/RAM/аптайм) — если бы он стоял первой строкой, весь системный
    промпт отличался бы от предыдущего с первого символа, и повторяющийся префикс
    (статичные правила, характер) никогда бы не совпадал между вызовами — а именно
    по совпадению префикса провайдер кэширует повторяющийся контекст и считает его
    дешевле. Стабильная часть — в начале, самое изменчивое — в конце."""
    template = PROMPT_PATH.read_text(encoding="utf-8")

    filled = template.format(
        user_name=memory["user"]["name"],
        user_nick=memory["user"]["nick"],
        home_name=memory["home"]["name"],
    )
    vitals = _vitals_line()
    return filled + _facts_block(topic) + (("\n" + vitals) if vitals else "")
