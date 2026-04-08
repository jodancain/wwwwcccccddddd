from app.storage.database import AppDatabase
from app.config.settings import get_settings

_db: AppDatabase | None = None


async def get_db() -> AppDatabase:
    global _db
    if _db is None:
        settings = get_settings()
        _db = AppDatabase(settings.db_path)
        await _db.connect()
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None
