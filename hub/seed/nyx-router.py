"""
Nyx — сердце хаба: чат, память, работа с DeepSeek API. Лежит в modules/nyx/
рядом с её же данными (memory.json/prompt.md/facts.json/replies.json), но
router.py и manifest.json — в write-денилисте apply_file_edit (см. core/tools.py):
физически рядом, редактировать может только git. Этот файл — только код,
данные — остальные файлы папки.
"""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from core.commands import handle_command
from core.compress import maybe_compress
from core.llm import DeepSeekError, ask_deepseek
from core.memory import get_current_topic, load_history, load_memory, load_summary, save_history
from core.system_prompt import build_system_prompt

router = APIRouter()

INBOX_DIR = Path("/app/modules/nyx/data/inbox")
OUTBOX_DIR = Path("/app/modules/nyx/data/outbox")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 МБ на файл — достаточно для текстов/логов/csv


def _safe_name(name: str) -> str:
    """Только имя файла, без пути — не даём вылезти за пределы inbox/outbox."""
    return Path(name).name


MEDIA_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp"},
    "audio": {".mp3", ".wav", ".ogg", ".m4a", ".flac"},
}


def _media_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    for kind, exts in MEDIA_EXTENSIONS.items():
        if ext in exts:
            return kind
    return "file"


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    status: str = "ok"


# Аварийный интерфейс — целиком встроен в Python-код этого модуля, не зависит
# ни от одного файла в modules/interface/. Даже если apply_file_edit полностью
# уничтожит index.html/app.js/style.css, эта страница продолжит работать —
# через неё всегда можно достучаться до Никс и попросить починить остальное.
_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NEXUS404 — аварийный чат</title>
<style>
  body { background:#111; color:#eee; font-family: monospace; padding:20px; max-width:700px; margin:0 auto; }
  h3 { color:#8ab4ff; font-weight:normal; }
  #log { white-space:pre-wrap; border:1px solid #444; padding:10px; height:60vh; overflow-y:auto; margin-bottom:10px; }
  input { width:78%; padding:8px; background:#000; color:#eee; border:1px solid #444; }
  button { padding:8px 16px; }
  .who-u { color:#8ab4ff; } .who-n { color:#9f9; } .who-e { color:#f66; }
</style>
</head>
<body>
<h3>NEXUS404 — аварийный интерфейс</h3>
<p style="color:#888;font-size:0.8em;">Не зависит от modules/interface — работает, даже если основной интерфейс сломан.</p>
<div id="log"></div>
<input id="msg" placeholder="сообщение..." autofocus>
<button onclick="send()">Отправить</button>
<script>
const log = document.getElementById('log');
const input = document.getElementById('msg');
function append(cls, who, text) {
    const div = document.createElement('div');
    div.innerHTML = '<span class="' + cls + '">' + who + ':</span> ' + text.replace(/</g, '&lt;');
    log.appendChild(div);
    log.appendChild(document.createElement('br'));
    log.scrollTop = log.scrollHeight;
}
async function send() {
    const message = input.value.trim();
    if (!message) return;
    append('who-u', 'ты', message);
    input.value = '';
    try {
        const r = await fetch('/api/nyx', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message})
        });
        const data = await r.json();
        append('who-n', 'никс', data.reply || JSON.stringify(data));
    } catch (e) {
        append('who-e', 'ошибка', e.message);
    }
}
input.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });
</script>
</body>
</html>"""


@router.get("/ui", response_class=HTMLResponse)
async def fallback_ui():
    return _FALLBACK_HTML


@router.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """Владимир прикрепляет 1+ файлов в чате. Сохраняем в inbox — дальше он
    просто пишет 'сравни файл1 и файл2', Никс сама читает их через
    list_inbox/read_file."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        content = await f.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{f.filename}: файл больше 10 МБ")
        name = _safe_name(f.filename or "файл")
        (INBOX_DIR / name).write_bytes(content)
        saved.append(name)
    return {"saved": saved}


@router.get("/files")
async def list_files_ui():
    """Для фронтенда: что лежит в inbox (загружено тобой) и outbox (готово
    от Никс — картинка/трек/файл, с типом для рендера в чате)."""
    def _list(d: Path):
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_file() and p.name != ".gitkeep")

    outbox = [{"name": n, "kind": _media_kind(n)} for n in _list(OUTBOX_DIR)]
    return {"inbox": _list(INBOX_DIR), "outbox": outbox}


@router.get("/media/{filename}")
async def view_media(filename: str):
    """Инлайн-показ (картинка/аудио прямо в чате) — без Content-Disposition:
    attachment, чтобы <img>/<audio> отрисовали содержимое, а не скачали файл."""
    target = OUTBOX_DIR / _safe_name(filename)
    if not target.is_file():
        raise HTTPException(404, "файл не найден в outbox")
    return FileResponse(target, content_disposition_type="inline")


@router.get("/download/{filename}")
async def download_output(filename: str):
    target = OUTBOX_DIR / _safe_name(filename)
    if not target.is_file():
        raise HTTPException(404, "файл не найден в outbox")
    return FileResponse(target, filename=target.name)


@router.get("/history")
async def get_chat_history():
    """Отдаёт сохранённую историю ТЕКУЩЕЙ темы — фронтенд рисует её при загрузке
    страницы или при переключении темы через /тема."""
    topic = get_current_topic()
    return {"topic": topic, "history": load_history(topic)}


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    cmd_reply = handle_command(request.message)
    if cmd_reply:
        return ChatResponse(reply=cmd_reply)

    memory = load_memory()
    topic = get_current_topic()
    history = load_history(topic)
    summary = load_summary(topic)

    messages = [{"role": "system", "content": build_system_prompt(memory, topic)}]
    if summary:
        messages.append({
            "role": "system",
            "content": f"Сводка более ранней части разговора в теме «{topic}» (старые сообщения сжаты сюда):\n{summary}",
        })
    # history хранит ещё и "at" (для отрисовки на фронтенде) — DeepSeek ожидает
    # только role/content, лишнее поле в теле запроса лучше не слать
    messages.extend({"role": h["role"], "content": h["content"]} for h in history)
    messages.append({"role": "user", "content": request.message})

    try:
        reply = await ask_deepseek(messages)
    except DeepSeekError as e:
        return ChatResponse(reply=str(e), status="error")
    except Exception as e:
        return ChatResponse(
            reply=f"Ошибка: {str(e)}. Проверь API-ключ или интернет.",
            status="error",
        )

    now = datetime.now().isoformat()
    history.append({"role": "user", "content": request.message, "at": now})
    history.append({"role": "assistant", "content": reply, "at": datetime.now().isoformat()})
    save_history(history, topic)

    await maybe_compress(topic)

    return ChatResponse(reply=reply)
