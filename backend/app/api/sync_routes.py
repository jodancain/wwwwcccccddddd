from fastapi import APIRouter

from app.dependencies import get_db
from app.sync.engine import sync_engine

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status")
async def get_sync_status():
    db = await get_db()
    state = await db.get_sync_state()
    return {
        "status": sync_engine.status,
        "last_sync_timestamp": state.get("last_sync_timestamp", 0),
        "last_sync_at": state.get("last_sync_at"),
        "total_messages": state.get("total_messages", 0),
    }


@router.post("/trigger")
async def trigger_sync():
    await sync_engine.force_sync()
    return {"message": "Sync triggered"}
