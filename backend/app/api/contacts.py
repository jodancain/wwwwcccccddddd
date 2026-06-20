from typing import Literal

from fastapi import APIRouter, Query

from app.dependencies import get_db

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("/")
async def get_contacts(
    search: str = "",
    contact_type: Literal["all", "friend", "group"] = Query("all", alias="type"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    db = await get_db()
    return await db.get_contacts(search, contact_type, limit, offset)
