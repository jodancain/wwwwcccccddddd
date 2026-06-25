"""Chat API management + Open API endpoints.

Management: create/list/delete API endpoints for conversations.
Open API: external access to conversation data via API key.
"""
import uuid
from typing import Any

from fastapi import APIRouter, Header, Query, HTTPException
from pydantic import BaseModel, Field
from loguru import logger

from app.agent.service import wechat_agent_service
from app.dependencies import get_db

# --- Management Router (internal, under /api/chat-apis/) ---
mgmt_router = APIRouter(prefix="/chat-apis", tags=["chat-apis"])


class CreateApiRequest(BaseModel):
    talker: str
    name: str = ""


class CreateAgentApiRequest(BaseModel):
    name: str = "External Agent API"
    permissions: list[str] = Field(default_factory=lambda: ["agent:chat"])


class OpenAgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


AGENT_TALKER = "__agent__"
OPEN_AGENT_SESSION_PREFIX = "__open_agent__:"
ALLOWED_API_PERMISSIONS = {"records:read", "agent:chat", "agent:confirm", "all"}


def _normalize_permissions(raw_permissions: list[str]) -> list[str]:
    permissions = [str(item).strip() for item in raw_permissions if str(item).strip()]
    if not permissions:
        permissions = ["agent:chat"]
    invalid = sorted(set(permissions) - ALLOWED_API_PERMISSIONS)
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid permissions: {', '.join(invalid)}")
    if "all" in permissions:
        return ["all"]
    return sorted(set(permissions))


def _scope_for_permissions(permissions: list[str]) -> str:
    if "all" in permissions:
        return "all"
    has_agent = any(item.startswith("agent:") for item in permissions)
    has_records = "records:read" in permissions
    if has_agent and has_records:
        return "all"
    return "agent" if has_agent else "records"


def _permissions(api_record: dict) -> set[str]:
    raw = str(api_record.get("permissions") or "").strip()
    if not raw:
        scope = str(api_record.get("scope") or "records")
        raw = "agent:chat" if scope == "agent" else "records:read"
    return {item.strip() for item in raw.split(",") if item.strip()}


def _has_permission(api_record: dict, permission: str) -> bool:
    permissions = _permissions(api_record)
    return "all" in permissions or permission in permissions


def _is_confirm_or_cancel_text(message: str) -> bool:
    stripped = message.strip().lower()
    if stripped.startswith(("confirm ", "cancel ")):
        return True
    return bool(stripped.startswith(("确认", "取消")))


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


@mgmt_router.post("/create-agent")
async def create_agent_api(req: CreateAgentApiRequest):
    db = await get_db()
    api_id = str(uuid.uuid4())[:8]
    api_key = f"wca_{uuid.uuid4().hex}"
    permissions = _normalize_permissions(req.permissions)
    result = await db.create_chat_api(
        api_id,
        AGENT_TALKER,
        api_key,
        req.name.strip() or "External Agent API",
        scope=_scope_for_permissions(permissions),
        permissions=",".join(permissions),
    )
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


async def _verify_api_key(
    authorization: str = Header(None),
    api_key: str = Query(None),
    required_permission: str = "",
):
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
    if required_permission and not _has_permission(api_record, required_permission):
        raise HTTPException(status_code=403, detail=f"API key missing permission: {required_permission}")
    await db.touch_chat_api(api_record["id"])

    return api_record


@open_router.get("/{api_id}/info")
async def open_api_info(
    api_id: str,
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Get conversation info."""
    api_record = await _verify_api_key(authorization, api_key, required_permission="records:read")
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
    api_record = await _verify_api_key(authorization, api_key, required_permission="records:read")
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
    api_record = await _verify_api_key(authorization, api_key, required_permission="records:read")
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
    api_record = await _verify_api_key(authorization, api_key, required_permission="records:read")
    if api_record["id"] != api_id:
        raise HTTPException(status_code=403, detail="API key does not match this endpoint.")

    db = await get_db()
    return await db.get_messages(
        talker=api_record["talker"],
        search=q,
        page_size=page_size,
    )


@open_router.get("/agent/status")
async def open_agent_status(
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Get external Agent API status for this key."""
    api_record = await _verify_api_key(authorization, api_key, required_permission="agent:chat")
    status = await wechat_agent_service.status()
    return {
        "api_id": api_record["id"],
        "name": api_record.get("name") or "",
        "scope": api_record.get("scope") or "agent",
        "permissions": sorted(_permissions(api_record)),
        "agent": {
            "enabled": status.get("enabled"),
            "transport_mode": status.get("transport_mode"),
            "router_mode": status.get("router_mode"),
            "dev_agent_version": status.get("dev_agent_version"),
            "permission_mode": status.get("permission_mode"),
        },
    }


@open_router.post("/agent/chat")
async def open_agent_chat(
    req: OpenAgentChatRequest,
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Chat with the same WeChatAI Agent used by the WeChat/OpenClaw entry."""
    api_record = await _verify_api_key(authorization, api_key, required_permission="agent:chat")
    if _is_confirm_or_cancel_text(req.message) and not _has_permission(api_record, "agent:confirm"):
        raise HTTPException(status_code=403, detail="API key missing permission: agent:confirm")

    db = await get_db()
    owner_talker = f"{OPEN_AGENT_SESSION_PREFIX}{api_record['id']}"
    session_id = req.session_id.strip()
    if session_id:
        session = await db.get_ai_session(session_id)
        if not session or session.get("talker") != owner_talker:
            raise HTTPException(status_code=403, detail="Session does not belong to this API key.")
    else:
        session_id = await db.create_ai_session(talker=owner_talker, title=f"Open Agent API - {api_record.get('name') or api_record['id']}")

    await db.save_ai_message(session_id, "user", req.message)
    result = await wechat_agent_service.handle_entry_text(req.message)
    reply = str(result.get("reply") or "")
    await db.save_ai_message(session_id, "assistant", reply)
    await db.add_agent_audit(
        "open_agent_chat",
        {
            "api_id": api_record["id"],
            "session_id": session_id,
            "status": result.get("status"),
            "route": result.get("agent_route"),
            "metadata": req.metadata,
        },
    )
    return {
        "session_id": session_id,
        "status": result.get("status", "ok"),
        "reply": reply,
        "used_claude": bool(result.get("used_claude")),
        "agent_route": result.get("agent_route"),
        "pending_actions": result.get("pending_actions") or ([result["pending_action"]] if result.get("pending_action") else []),
    }


@open_router.get("/agent/sessions/{session_id}/messages")
async def open_agent_session_messages(
    session_id: str,
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    api_record = await _verify_api_key(authorization, api_key, required_permission="agent:chat")
    db = await get_db()
    session = await db.get_ai_session(session_id)
    if not session or session.get("talker") != f"{OPEN_AGENT_SESSION_PREFIX}{api_record['id']}":
        raise HTTPException(status_code=403, detail="Session does not belong to this API key.")
    return await db.get_ai_messages(session_id)


@open_router.post("/agent/actions/{action_id}/confirm")
async def open_agent_confirm_action(
    action_id: str,
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    await _verify_api_key(authorization, api_key, required_permission="agent:confirm")
    return await wechat_agent_service.confirm_action(action_id)


@open_router.post("/agent/actions/{action_id}/cancel")
async def open_agent_cancel_action(
    action_id: str,
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    await _verify_api_key(authorization, api_key, required_permission="agent:confirm")
    return await wechat_agent_service.cancel_action(action_id)
