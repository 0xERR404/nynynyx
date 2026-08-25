from datetime import datetime

from fastapi import APIRouter

from core.llm import DEEPSEEK_API_KEY
from core.memory import load_history, load_memory
from core.system_stats import get_system_stats

router = APIRouter()


@router.get("/status")
async def status():
    memory = load_memory()
    return {
        "status": "alive",
        "memory_loaded": bool(memory),
        "api_key_configured": bool(DEEPSEEK_API_KEY),
        "history_length": len(load_history()),
        "time": datetime.now().isoformat(),
    }


@router.get("/system")
async def system():
    return get_system_stats()
