from __future__ import annotations

import json
import re
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.service import wechat_agent_service


router = APIRouter(prefix="/relay/v1", tags=["local-relay"])


class RelayMessagesRequest(BaseModel):
    model: str = "wechatai-direct-agent"
    messages: list[dict[str, Any]] = Field(..., min_length=1)
    system: Any = ""
    stream: bool = False
    max_tokens: int = 8192


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
            parts.append(str(block["text"]))
    return "\n".join(parts).strip()


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if str(message.get("role") or "").lower() != "user":
            continue
        text = _content_text(message.get("content"))
        if text:
            return text
    return ""


_OPENCLAW_CONTEXT_PREFIX = re.compile(
    r"^\s*(?:\[[^\]\r\n]{1,160}\]\s*)?"
    r"Conversation info \(untrusted metadata\):\s*"
    r"```(?:json)?\s*.*?\s*```\s*",
    flags=re.IGNORECASE | re.DOTALL,
)


def _clean_relay_user_text(text: str) -> str:
    """Remove OpenClaw's transport envelope before intent routing."""
    original = text.strip()
    cleaned = _OPENCLAW_CONTEXT_PREFIX.sub("", original, count=1).strip()
    return cleaned or original


def _usage(text: str) -> dict[str, int]:
    return {"input_tokens": 1, "output_tokens": max(1, len(text) // 3)}


def _message_payload(message_id: str, model: str, text: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": model,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": _usage(text),
    }


async def _stream_payload(message_id: str, model: str, text: str) -> AsyncIterator[str]:
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        ),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": _usage(text)["output_tokens"]}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    for event, payload in events:
        yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/models")
async def relay_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "wechatai-direct-agent",
                "object": "model",
                "display_name": "WeChatAI Direct Agent",
            }
        ],
    }


@router.post("/messages")
async def relay_messages(req: RelayMessagesRequest):
    user_text = _clean_relay_user_text(_latest_user_text(req.messages))
    if not user_text:
        raise HTTPException(status_code=400, detail="No user text was found in messages")

    result = await wechat_agent_service.handle_entry_text(
        user_text,
        dialog_key="agent.openclaw.dialog_session_id",
    )
    reply = str(result.get("reply") or result.get("message") or "").strip()
    if not reply:
        raise HTTPException(status_code=502, detail="WeChatAI Agent returned an empty reply")

    message_id = f"msg_{uuid.uuid4().hex}"
    model = req.model or "wechatai-direct-agent"
    if req.stream:
        return StreamingResponse(
            _stream_payload(message_id, model, reply),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    return _message_payload(message_id, model, reply)
