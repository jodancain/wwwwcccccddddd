from fastapi import APIRouter
from pydantic import BaseModel

from app.dependencies import get_db
from app.config.settings import get_settings

router = APIRouter(prefix="/settings", tags=["settings"])

SENSITIVE_KEYS = {"GEMINI_API_KEY", "OPENAI_API_KEY", "EMAIL_PASSWORD"}


@router.get("/")
async def get_all_settings():
    settings = get_settings()
    return {
        "AI_PROVIDER": settings.AI_PROVIDER,
        "GEMINI_API_KEY": "***" + settings.GEMINI_API_KEY[-4:] if len(settings.GEMINI_API_KEY) > 4 else "",
        "GEMINI_MODEL": settings.GEMINI_MODEL,
        "OPENAI_API_KEY": "***" + settings.OPENAI_API_KEY[-4:] if len(settings.OPENAI_API_KEY) > 4 else "",
        "OPENAI_BASE_URL": settings.OPENAI_BASE_URL,
        "OPENAI_MODEL": settings.OPENAI_MODEL,
        "SYNC_INTERVAL_SECONDS": settings.SYNC_INTERVAL_SECONDS,
        "DECRYPT_INTERVAL_SECONDS": settings.DECRYPT_INTERVAL_SECONDS,
    }


@router.get("/wechat/status")
async def get_wechat_status():
    settings = get_settings()
    decrypted_dir = settings.decrypted_wx_dir
    db_files = list(decrypted_dir.glob("*.db"))

    running = False
    try:
        import wdecipher
        infos = wdecipher.get_wx_infos()
        running = bool(infos)
    except Exception:
        pass

    return {
        "wechat_running": running,
        "decrypted_db_count": len(db_files),
        "decrypted_dir": str(decrypted_dir),
    }
