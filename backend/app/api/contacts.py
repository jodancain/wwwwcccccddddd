from fastapi import APIRouter, Query

from app.dependencies import get_db

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("/")
async def get_contacts(
    search: str = "",
    type: str = "all",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    db = await get_db()
    return await db.get_contacts(search, type, limit, offset)
