"""Chat API management + Open API endpoints.

Management: create/list/delete API endpoints for conversations.
Open API: external access to conversation data via API key.
"""
import uuid

from fastapi import APIRouter, Header, Query, HTTPException
from pydantic import BaseModel
from loguru import logger

from app.dependencies import get_db

# --- Management Router (internal, under /api/chat-apis/) ---
mgmt_router = APIRouter(prefix="/chat-apis", tags=["chat-apis"])


class CreateApiRequest(BaseModel):
    talker: str
    name: str = ""


@mgmt_router.get("/")
async def list_apis():
    db = await get_db()
    apis = await db.list_chat_apis()
    masked = []
    for api in apis:
        item = dict(api)
        key = item.pop("api_key", "")
        item["api_key_preview"] = f"...{key[-8:]}" if len(key) > 8 else key
        masked.append(item)
    return masked


@mgmt_router.post("/create")
async def create_api(req: CreateApiRequest):
    db = await get_db()
    api_id = str(uuid.uuid4())[:8]
    api_key = f"wca_{uuid.uuid4().hex}"

    # Get contact name if not provided
    name = req.name
    if not name:
        convs = await db.get_conversations()
        for c in convs:
            if c["talker"] == req.talker:
                name = c.get("remark") or c.get("nickname") or req.talker
                break

    result = await db.create_chat_api(api_id, req.talker, api_key, name)
    return result


@mgmt_router.delete("/{api_id}")
async def delete_api(api_id: str):
    db = await get_db()
    ok = await db.delete_chat_api(api_id)
    return {"success": ok}


@mgmt_router.post("/{api_id}/toggle")
async def toggle_api(api_id: str):
    db = await get_db()
    result = await db.toggle_chat_api(api_id)
    if not result:
        raise HTTPException(status_code=404, detail="API not found")
    return result


# --- Open API Router (external, under /open/v1/) ---
open_router = APIRouter(prefix="/open/v1", tags=["open-api"])


async def _verify_api_key(authorization: str = Header(None), api_key: str = Query(None)):
    """Verify API key from header or query param."""
    key = None
    if authorization and authorization.startswith("Bearer "):
        key = authorization[7:]
    elif api_key:
        key = api_key

    if not key:
        raise HTTPException(status_code=401, detail="Missing API key. Use 'Authorization: Bearer <key>' header or '?api_key=<key>' query param.")

    db = await get_db()
    api_record = await db.get_chat_api_by_key(key)
    if not api_record:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key.")

    return api_record


@open_router.get("/{api_id}/info")
async def open_api_info(
    api_id: str,
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Get conversation info."""
    api_record = await _verify_api_key(authorization, api_key)
    if api_record["id"] != api_id:
        raise HTTPException(status_code=403, detail="API key does not match this endpoint.")

    db = await get_db()
    convs = await db.get_conversations()
    for c in convs:
        if c["talker"] == api_record["talker"]:
            return {
                "name": api_record["name"],
                "talker": c["talker"],
                "nickname": c.get("nickname", ""),
                "remark": c.get("remark", ""),
                "is_group": c.get("is_group", 0),
                "msg_count": c.get("msg_count", 0),
                "last_time": c.get("last_time", 0),
            }
    return {"name": api_record["name"], "talker": api_record["talker"]}


@open_router.get("/{api_id}/messages")
async def open_api_messages(
    api_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    date: str = "",
    search: str = "",
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Get messages for this conversation."""
    api_record = await _verify_api_key(authorization, api_key)
    if api_record["id"] != api_id:
        raise HTTPException(status_code=403, detail="API key does not match this endpoint.")

    db = await get_db()
    return await db.get_messages(
        talker=api_record["talker"],
        date=date,
        search=search,
        page=page,
        page_size=page_size,
    )


@open_router.get("/{api_id}/messages/recent")
async def open_api_recent(
    api_id: str,
    limit: int = Query(50, ge=1, le=500),
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Get most recent messages."""
    api_record = await _verify_api_key(authorization, api_key)
    if api_record["id"] != api_id:
        raise HTTPException(status_code=403, detail="API key does not match this endpoint.")

    db = await get_db()
    return await db.get_recent_messages_for_talker(api_record["talker"], limit)


@open_router.get("/{api_id}/search")
async def open_api_search(
    api_id: str,
    q: str = Query(..., min_length=1),
    page_size: int = Query(50, ge=1, le=200),
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Search messages in this conversation."""
    api_record = await _verify_api_key(authorization, api_key)
    if api_record["id"] != api_id:
        raise HTTPException(status_code=403, detail="API key does not match this endpoint.")

    db = await get_db()
    return await db.get_messages(
        talker=api_record["talker"],
        search=q,
        page_size=page_size,
    )
