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
from app.knowledge.embedding import embedding_client
from app.knowledge.indexer import knowledge_indexer
from app.knowledge.source_extractor import source_extractor

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
    detail_level: str = "comprehensive"


class OpenKnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(8, ge=1, le=30)
    talker: str = ""
    use_embedding: bool = True


AGENT_TALKER = "__agent__"
OPEN_AGENT_SESSION_PREFIX = "__open_agent__:"
ALLOWED_API_PERMISSIONS = {
    "records:read",
    "agent:chat",
    "agent:confirm",
    "knowledge:read",
    "knowledge:write",
    "project:read",
    "all",
}


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
    has_project = any(item.startswith(("knowledge:", "project:")) for item in permissions)
    if sum([has_agent, has_records, has_project]) > 1:
        return "all"
    if has_project:
        return "project"
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


def _open_agent_detail_instruction(req: OpenAgentChatRequest) -> str:
    raw_level = req.detail_level or req.metadata.get("detail_level") or "comprehensive"
    level = str(raw_level).strip().lower()
    if level in {"short", "brief", "concise", "simple"}:
        return ""
    schema_hint = req.metadata.get("response_schema") or req.metadata.get("schema") or ""
    domain_hint = req.metadata.get("domain") or ""
    wants_json = "json" in req.message.lower() or bool(schema_hint)
    strict_json = ""
    if wants_json:
        strict_json = (
            "STRICT OUTPUT FORMAT: Return ONLY valid JSON. Do not wrap it in Markdown. "
            "Do not add prose before or after the JSON. Expand the JSON schema with detailed nested fields when needed.\n"
        )
    return (
        "\n\n[Open API response policy]\n"
        f"{strict_json}"
        "Default to a comprehensive, evidence-first answer. Do not return a thin summary.\n"
        "Use the full WeChatAI project context and knowledge base whenever relevant.\n"
        "If the caller asks for JSON, keep valid JSON but make every field detailed: include detailed_summary, "
        "topic_breakdown, evidence_items with source chat/contact/date/snippet, risks, uncertainties, next_actions, "
        "and open_questions when applicable.\n"
        "For investment/trading outputs, do not produce only one generic signal unless only one item is truly supported. "
        "For each signal include symbol, side, conviction, rationale, evidence, counter_evidence_or_uncertainty, risk, "
        "what_to_watch_next, and source_messages. Summaries should explain who discussed it, what exactly was said, "
        "why it matters, and what is not yet confirmed.\n"
        "If evidence is sparse, say so explicitly and still provide the supporting snippets and missing information.\n"
        f"Requested detail_level: {level or 'comprehensive'}.\n"
        f"Optional domain hint: {domain_hint}.\n"
        f"Optional response schema hint: {schema_hint}.\n"
    )


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


@open_router.get("/project/capabilities")
async def open_project_capabilities(
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """List the external API surface available to this API key."""
    api_record = await _verify_api_key(authorization, api_key, required_permission="project:read")
    permissions = sorted(_permissions(api_record))
    return {
        "api_id": api_record["id"],
        "name": api_record.get("name") or "",
        "permissions": permissions,
        "auth": {
            "header": "Authorization: Bearer <api_key>",
            "query": "?api_key=<api_key>",
        },
        "endpoints": [
            {"method": "GET", "path": "/open/v1/project/status", "permission": "project:read"},
            {"method": "GET", "path": "/open/v1/project/capabilities", "permission": "project:read"},
            {"method": "GET", "path": "/open/v1/knowledge/status", "permission": "knowledge:read"},
            {"method": "POST", "path": "/open/v1/knowledge/search", "permission": "knowledge:read"},
            {"method": "POST", "path": "/open/v1/knowledge/enrich-now", "permission": "knowledge:write"},
            {"method": "POST", "path": "/open/v1/knowledge/index-now", "permission": "knowledge:write"},
            {"method": "POST", "path": "/open/v1/knowledge/embed-now", "permission": "knowledge:write"},
            {"method": "GET", "path": "/open/v1/agent/status", "permission": "agent:chat"},
            {"method": "POST", "path": "/open/v1/agent/chat", "permission": "agent:chat"},
            {"method": "GET", "path": "/open/v1/records/conversations", "permission": "records:read"},
            {"method": "GET", "path": "/open/v1/records/recent", "permission": "records:read"},
            {"method": "GET", "path": "/open/v1/records/global-search", "permission": "records:read"},
            {"method": "GET", "path": "/open/v1/records/by-talker", "permission": "records:read"},
            {"method": "GET", "path": "/open/v1/{api_id}/messages", "permission": "records:read"},
            {"method": "GET", "path": "/open/v1/{api_id}/messages/recent", "permission": "records:read"},
            {"method": "GET", "path": "/open/v1/{api_id}/search", "permission": "records:read"},
        ],
    }


@open_router.get("/project/status")
async def open_project_status(
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Get a safe external overview of the WeChatAI project runtime."""
    api_record = await _verify_api_key(authorization, api_key, required_permission="project:read")
    db = await get_db()
    agent_status = await wechat_agent_service.status()
    knowledge_status = await db.get_knowledge_status()
    sync_state = await db.get_sync_state()
    return {
        "api_id": api_record["id"],
        "name": api_record.get("name") or "",
        "permissions": sorted(_permissions(api_record)),
        "project": {
            "name": "WeChatAI",
            "open_api_version": "v1",
            "runtime": "local",
        },
        "agent": {
            "enabled": agent_status.get("enabled"),
            "transport_mode": agent_status.get("transport_mode"),
            "router_mode": agent_status.get("router_mode"),
            "dev_agent_version": agent_status.get("dev_agent_version"),
            "openclaw_forward_ready": agent_status.get("openclaw_forward_ready"),
        },
        "knowledge": {
            "chunks": knowledge_status.get("chunks"),
            "embedded_chunks": knowledge_status.get("embedded_chunks"),
            "messages": knowledge_status.get("messages"),
            "caught_up": knowledge_status.get("caught_up"),
            "embeddings_caught_up": knowledge_status.get("embeddings_caught_up"),
            "last_indexed_message_id": knowledge_status.get("last_indexed_message_id"),
            "max_message_id": knowledge_status.get("max_message_id"),
            "embedding_enabled": embedding_client.configured,
            "embedding_model": embedding_client.model,
            "indexer_status": knowledge_indexer.status,
            "embedding_status": knowledge_indexer.embedding_status,
        },
        "sync": {
            "last_sync_timestamp": sync_state.get("last_sync_timestamp"),
            "last_sync_at": sync_state.get("last_sync_at"),
            "last_msg_count": sync_state.get("last_msg_count"),
            "total_messages": sync_state.get("total_messages"),
        },
    }


@open_router.get("/knowledge/status")
async def open_knowledge_status(
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Get knowledge-base status through the external API."""
    await _verify_api_key(authorization, api_key, required_permission="knowledge:read")
    db = await get_db()
    status = await db.get_knowledge_status()
    status["indexer"] = {
        "running": knowledge_indexer._running,
        "status": knowledge_indexer.status,
        "embedding_status": knowledge_indexer.embedding_status,
        "last_error": knowledge_indexer.last_error,
        "last_embedding_error": knowledge_indexer.last_embedding_error,
        "interval_seconds": knowledge_indexer.interval_seconds,
    }
    status["embedding"] = {
        "enabled": embedding_client.configured,
        "model": embedding_client.model,
        "base_url": embedding_client.base_url,
    }
    return status


@open_router.post("/knowledge/search")
async def open_knowledge_search(
    req: OpenKnowledgeSearchRequest,
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Search the full WeChatAI knowledge base through the external API."""
    await _verify_api_key(authorization, api_key, required_permission="knowledge:read")
    db = await get_db()
    results = await db.search_knowledge(req.query, limit=req.limit, talker=req.talker)
    if req.use_embedding and embedding_client.configured:
        vectors = await embedding_client.embed_texts([req.query], input_type="query")
        vector_results = await db.search_knowledge_vector(
            vectors[0],
            model=embedding_client.model,
            limit=req.limit,
            talker=req.talker,
        )
        merged = {int(item.get("id") or 0): item for item in results}
        for item in vector_results:
            chunk_id = int(item.get("id") or 0)
            if chunk_id not in merged:
                merged[chunk_id] = item
        results = sorted(
            merged.values(),
            key=lambda item: (
                1 if item.get("retrieval") == "embedding" else 0,
                float(item.get("score") or 0),
                int(item.get("end_time") or 0),
            ),
            reverse=True,
        )[: req.limit]
    return {"query": req.query, "count": len(results), "items": results}


@open_router.post("/knowledge/enrich-now")
async def open_knowledge_enrich_now(
    hours: int = Query(24, ge=0, le=720),
    limit: int = Query(2000, ge=0, le=100000),
    max_links: int = Query(80, ge=0, le=500),
    max_images: int = Query(20, ge=0, le=100),
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Parse recent links/images into reusable knowledge sources."""
    await _verify_api_key(authorization, api_key, required_permission="knowledge:write")
    db = await get_db()
    messages = await db.get_all_recent_messages(hours=hours, limit=limit)
    enriched = await source_extractor.enrich_messages(
        db,
        messages,
        max_links=max_links,
        max_images=max_images,
    )
    source_messages = [item for item in enriched if item.get("source_enrichments")]
    return {
        "status": "ok",
        "hours": hours,
        "messages_checked": len(messages),
        "messages_with_sources": len(source_messages),
        "source_enrichments": sum(len(item.get("source_enrichments") or []) for item in enriched),
    }


@open_router.post("/knowledge/index-now")
async def open_knowledge_index_now(
    limit: int = Query(5000, ge=1, le=50000),
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Index newly synced messages into the knowledge base."""
    await _verify_api_key(authorization, api_key, required_permission="knowledge:write")
    return await knowledge_indexer.index_new_messages(limit=limit)


@open_router.post("/knowledge/embed-now")
async def open_knowledge_embed_now(
    limit: int = Query(64, ge=1, le=256),
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Embed pending knowledge chunks."""
    await _verify_api_key(authorization, api_key, required_permission="knowledge:write")
    return await knowledge_indexer.embed_new_chunks(limit=limit)


@open_router.get("/records/conversations")
async def open_records_conversations(
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """List all synced WeChat conversations."""
    await _verify_api_key(authorization, api_key, required_permission="records:read")
    db = await get_db()
    return await db.get_conversations()


@open_router.get("/records/recent")
async def open_records_recent(
    hours: int = Query(24, ge=0, le=720),
    limit: int = Query(500, ge=0, le=100000),
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Get recent messages across all conversations."""
    await _verify_api_key(authorization, api_key, required_permission="records:read")
    db = await get_db()
    items = await db.get_all_recent_messages(hours=hours, limit=limit)
    return {"hours": hours, "count": len(items), "items": items}


@open_router.get("/records/messages")
async def open_records_messages(
    talker: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    date: str = "",
    search: str = "",
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Get paginated messages for any conversation by talker id."""
    await _verify_api_key(authorization, api_key, required_permission="records:read")
    db = await get_db()
    return await db.get_messages(
        talker=talker,
        date=date,
        search=search,
        page=page,
        page_size=page_size,
    )


@open_router.get("/records/by-talker")
async def open_records_by_talker(
    talker: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    date: str = "",
    search: str = "",
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Get paginated messages for any conversation by talker id."""
    return await open_records_messages(
        talker=talker,
        page=page,
        page_size=page_size,
        date=date,
        search=search,
        authorization=authorization,
        api_key=api_key,
    )


@open_router.get("/records/search")
async def open_records_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(100, ge=1, le=1000),
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Search raw messages across the whole project."""
    await _verify_api_key(authorization, api_key, required_permission="records:read")
    db = await get_db()
    rows = await db._db.execute_fetchall(
        """SELECT m.id, m.wechat_local_id, m.talker, m.sender, m.type, m.type_name,
                  m.is_sender, m.content, m.display_content, m.create_time,
                  m.create_date, m.is_group, c.nickname, c.remark
           FROM messages m
           LEFT JOIN contacts c ON m.talker = c.username
           WHERE m.content LIKE ? OR m.display_content LIKE ?
           ORDER BY m.create_time DESC
           LIMIT ?""",
        (f"%{q}%", f"%{q}%", limit),
    )
    items = [dict(row) for row in rows]
    return {"query": q, "count": len(items), "items": items}


@open_router.get("/records/global-search")
async def open_records_global_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(100, ge=1, le=1000),
    authorization: str = Header(None),
    api_key: str = Query(None),
):
    """Search raw messages across the whole project."""
    return await open_records_search(
        q=q,
        limit=limit,
        authorization=authorization,
        api_key=api_key,
    )


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
    agent_message = req.message + _open_agent_detail_instruction(req)
    result = await wechat_agent_service.handle_entry_text(
        agent_message,
        dialog_key=f"open_agent.dialog_session_id.{api_record['id']}",
    )
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
            "detail_level": req.detail_level,
        },
    )
    return {
        "session_id": session_id,
        "status": result.get("status", "ok"),
        "reply": reply,
        "used_claude": bool(result.get("used_claude")),
        "agent_route": result.get("agent_route"),
        "detail_level": req.detail_level,
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
