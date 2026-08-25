import asyncio
import json
import os

import httpx

from core.tools import TOOL_FUNCTIONS, TOOLS_SCHEMA
from core.audit import log_tool_call

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

MAX_TOOL_ROUNDS = 8
MAX_TOKENS = 4096


class DeepSeekError(Exception):
    """Ожидаемая ошибка (нет ключа и т.п.) — показываем текст пользователю как есть."""


async def _call_deepseek(client: httpx.AsyncClient, messages: list, allow_tools: bool = True) -> dict:
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": MAX_TOKENS,
    }
    if allow_tools:
        payload["tools"] = TOOLS_SCHEMA

    response = await client.post(
        DEEPSEEK_URL,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    response.raise_for_status()
    return response.json()


def _finalize(text: str, real_edits: list) -> str:
    """Не доверяем пересказу модели — единственный источник правды: список
    правок, реально записанных на диск (apply_file_edit/patch_file/delete_path)
    в рамках этого ответа."""
    text = (text or "").rstrip()

    if real_edits:
        hot_reloaded = [e for e in real_edits if e.get("hot_reloaded")]
        lines = [f"- modules/{e['path']}" for e in real_edits]
        note = "\n\n[подтверждено хабом — реально записано на диск]\n" + "\n".join(lines)
        if hot_reloaded:
            note += "\n\nЗатронутый модуль поднялся заново сам, за пару секунд, без рестарта хаба."
        else:
            note += "\n\nПрименилось мгновенно."
        return text + note

    # Предупреждение о "вранье" должно срабатывать только когда текст ПОХОЖ на заявление
    # о применённой правке файла — а не на любое использование обычных русских слов вроде
    # "сделала"/"поменял" в живой речи. Поэтому требуем совпадения из ДВУХ групп сразу.
    completion_words = ("готово", "применил", "поменял", "исправил", "сделала", "изменил", "обновил")
    edit_context_words = ("файл", "модул", "интерфейс", "правк", "css", "html", "app.js", "style.css", "index.html")
    lowered = text.lower()
    claims_done = (
        any(word in lowered for word in completion_words)
        and any(word in lowered for word in edit_context_words)
    )
    if claims_done:
        return (
            text
            + "\n\n⚠️ Внутренняя проверка хаба: в этом ответе ни один инструмент правки фактически "
              "не сработал. Файлы не менялись, что бы ни было написано выше."
        )

    return text


async def summarize_messages(old_summary: str, messages: list) -> str:
    """Сжимает кусок переписки в компактную сводку — отдельный, простой вызов
    DeepSeek без function calling. Используется core/compress.py, когда история
    темы разрастается."""
    if not DEEPSEEK_API_KEY:
        raise DeepSeekError("нет API-ключа для сжатия истории")

    convo_text = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    prompt = (
        "Сожми следующий фрагмент переписки в краткую сводку на русском (5-10 предложений). "
        "Сохрани важные факты, договорённости, предпочтения пользователя, принятые решения. "
        "Не пересказывай дословно, выдели суть.\n\n"
    )
    if old_summary:
        prompt += f"Предыдущая сводка:\n{old_summary}\n\n"
    prompt += f"Новый фрагмент переписки:\n{convo_text}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 500,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()


async def ask_deepseek(messages: list) -> str:
    if not DEEPSEEK_API_KEY:
        raise DeepSeekError("API-ключ не найден. Проверь .env файл с DEEPSEEK_API_KEY.")

    working_messages = list(messages)
    real_edits = []  # правки, реально записанные на диск за этот ответ

    async with httpx.AsyncClient(timeout=30.0) as client:
        for round_index in range(MAX_TOOL_ROUNDS):
            is_last_round = round_index == MAX_TOOL_ROUNDS - 1

            # На последнем круге принудительно отбираем инструменты — без них
            # DeepSeek физически не может вернуть tool_calls и обязан ответить
            # обычным текстом. Это гарантирует реальный ответ вместо заглушки,
            # даже если модель почему-то не может "остановиться" сама.
            data = await _call_deepseek(client, working_messages, allow_tools=not is_last_round)
            choice_message = data["choices"][0]["message"]

            tool_calls = choice_message.get("tool_calls")
            if not tool_calls or is_last_round:
                return _finalize(choice_message.get("content") or "", real_edits)

            working_messages.append(choice_message)

            for call in tool_calls:
                fn_name = call["function"]["name"]
                raw_args = call["function"].get("arguments") or "{}"
                try:
                    fn_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    working_messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps({
                            "error": "аргументы вызова обрезаны или повреждены — вероятно, содержимое файла "
                                     "слишком длинное для одного ответа. Попробуй разбить правку на меньшие "
                                     "куски или сократить контекст."
                        }, ensure_ascii=False),
                    })
                    continue

                fn = TOOL_FUNCTIONS.get(fn_name)
                if fn:
                    # apply_file_edit/delete_path могут поднимать subprocess модуля
                    # с health-check внутри (блокирующий sleep до 5 сек) — в отдельном
                    # потоке, а не в event loop'е, иначе на это время замирает ВЕСЬ
                    # хаб: статика, другие модули, параллельные запросы к чату.
                    result = await asyncio.to_thread(fn, fn_args)
                else:
                    result = {"error": f"неизвестный инструмент: {fn_name}"}

                log_tool_call(fn_name, fn_args, result)

                # Реальное подтверждение правки нужно от ЛЮБОГО инструмента, который
                # реально пишет на диск — не только apply_file_edit. Раньше patch_file
                # (и delete_path) сюда не попадали: правка проходила по-настоящему, но
                # подтверждающая плашка не появлялась, модель терялась и вместо честного
                # "не уверена, применилось ли" начинала выдумывать несуществующие
                # "системные проверки хаба" — это было реальным источником вранья.
                if fn_name in ("apply_file_edit", "patch_file") and result.get("applied"):
                    real_edits.append({"path": result["path"], "hot_reloaded": result.get("hot_reloaded", False)})
                elif fn_name == "delete_path" and result.get("deleted"):
                    real_edits.append({"path": f"[удалено] {result['path']}", "hot_reloaded": False})

                working_messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

        return _finalize(
            "Заглянула в несколько файлов, но так и не разобралась за отведённое число шагов — уточни, что именно посмотреть.",
            real_edits,
        )
