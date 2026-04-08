from fastapi import APIRouter, Query

from app.dependencies import get_db

router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("/conversations")
async def get_conversations(search: str = ""):
    db = await get_db()
    return await db.get_conversations(search)


@router.get("/")
async def get_messages(
    talker: str = "",
    date: str = "",
    search: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    db = await get_db()
    return await db.get_messages(talker, date, search, page, page_size)


@router.get("/dates")
async def get_message_dates(talker: str = ""):
    if not talker:
        return []
    db = await get_db()
    return await db.get_message_dates(talker)


@router.get("/by-date")
async def get_messages_by_date(
    talker: str = "",
    date: str = "",
    page_size: int = Query(100, ge=1, le=200),
):
    if not talker or not date:
        return {"total": 0, "offset": 0, "items": []}
    db = await get_db()
    return await db.get_messages_by_date(talker, date, page_size)


@router.get("/recent")
async def get_recent_messages(talker: str = "", limit: int = Query(50, ge=1, le=200)):
    db = await get_db()
    return await db.get_recent_messages_for_talker(talker, limit)
