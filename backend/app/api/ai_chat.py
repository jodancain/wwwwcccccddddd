import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from app.ai.context_builder import (
    GLOBAL_SUMMARY_SYSTEM_PROMPT,
    SUGGEST_REPLY_PROMPT,
    SYSTEM_PROMPT,
    build_conversation_context,
    build_global_context,
)
from app.ai.fallback import fallback_chat_response, fallback_global_summary, fallback_replies
from app.ai.gemini_provider import GeminiProvider
from app.ai.openai_provider import OpenAIProvider
from app.ai.provider_base import AIProvider
from app.config.settings import get_settings
from app.dependencies import get_db
from app.skills.manager import build_skill_system_prompt

router = APIRouter(prefix="/ai", tags=["ai"])


def get_ai_provider() -> AIProvider:
    settings = get_settings()
    if settings.AI_PROVIDER == "openai":
        return OpenAIProvider()
    return GeminiProvider()


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    talker: str = ""
    active_skill: str = ""


class SuggestRequest(BaseModel):
    talker: str


class GlobalSummaryRequest(BaseModel):
    hours: int = 24
    message: str = ""


def _build_system_prompt(active_skill: str = "") -> str:
    sys_prompt = SYSTEM_PROMPT
    if active_skill:
        skill_prompt = build_skill_system_prompt(active_skill)
        if skill_prompt:
            sys_prompt = f"{SYSTEM_PROMPT}\n\n{skill_prompt}"
    return sys_prompt


async def _conversation_messages(talker: str) -> tuple[list[dict], str]:
    if not talker:
        return [], ""

    db = await get_db()
    messages = await db.get_all_messages_for_talker(talker)
    if not messages:
        return [], ""

    talker_name = messages[0].get("remark") or messages[0].get("nickname") or talker
    is_group = bool(messages[0].get("is_group"))
    context = build_conversation_context(messages, talker_name, is_group)
    return messages, context


async def _chat_messages(req: ChatRequest, session_id: str) -> tuple[list[dict], list[dict]]:
    db = await get_db()
    conversation, context = await _conversation_messages(req.talker)
    ai_history = await db.get_ai_messages(session_id)

    ai_messages = []
    if context:
        ai_messages.append({"role": "user", "content": f"以下是当前微信对话上下文：\n\n{context}"})
        ai_messages.append({"role": "assistant", "content": "好的，我已经看到这段对话内容。请问有什么需要我帮忙的？"})

    for msg in ai_history:
        ai_messages.append({"role": msg["role"], "content": msg["content"]})

    ai_messages.append({"role": "user", "content": req.message})
    return conversation, ai_messages


@router.post("/chat")
async def ai_chat(req: ChatRequest):
    db = await get_db()
    provider = get_ai_provider()

    session_id = req.session_id or await db.create_ai_session(talker=req.talker)
    conversation, ai_messages = await _chat_messages(req, session_id)

    try:
        response = await provider.chat(ai_messages, system_prompt=_build_system_prompt(req.active_skill))
    except Exception as exc:
        logger.error(f"AI chat error: {exc}")
        response = fallback_chat_response(req.message, conversation, exc)

    await db.save_ai_message(session_id, "user", req.message)
    await db.save_ai_message(session_id, "assistant", response)

    return {"session_id": session_id, "response": response}


@router.post("/chat/stream")
async def ai_chat_stream(req: ChatRequest):
    db = await get_db()
    provider = get_ai_provider()

    session_id = req.session_id or await db.create_ai_session(talker=req.talker)
    conversation, ai_messages = await _chat_messages(req, session_id)
    sys_prompt = _build_system_prompt(req.active_skill)

    async def generate():
        full_response = ""
        try:
            async for chunk in provider.chat_stream(ai_messages, system_prompt=sys_prompt):
                full_response += chunk
                yield f"data: {json.dumps({'chunk': chunk, 'session_id': session_id}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.error(f"Stream error: {exc}")
            full_response = fallback_chat_response(req.message, conversation, exc)
            yield f"data: {json.dumps({'chunk': full_response, 'session_id': session_id}, ensure_ascii=False)}\n\n"

        await db.save_ai_message(session_id, "user", req.message)
        await db.save_ai_message(session_id, "assistant", full_response)
        yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/suggest-replies")
async def suggest_replies(req: SuggestRequest):
    db = await get_db()
    provider = get_ai_provider()

    messages = await db.get_all_messages_for_talker(req.talker)
    if not messages:
        return {"replies": fallback_replies([])}

    talker_name = messages[0].get("remark") or messages[0].get("nickname") or req.talker
    is_group = bool(messages[0].get("is_group"))
    context = build_conversation_context(messages, talker_name, is_group)

    ai_messages = [{"role": "user", "content": f"{context}\n\n{SUGGEST_REPLY_PROMPT}"}]

    try:
        response = await provider.chat(ai_messages, system_prompt="你是一个微信回复建议生成器。只输出 JSON 数组格式的回复建议。")
        text = response.strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            replies = json.loads(text[start:end])
            return {"replies": replies[:3]}
    except Exception as exc:
        logger.error(f"Suggest replies error: {exc}")

    return {"replies": fallback_replies(messages)}


@router.post("/global-summary/stream")
async def global_summary_stream(req: GlobalSummaryRequest):
    db = await get_db()
    provider = get_ai_provider()

    all_messages = await db.get_all_recent_messages(hours=req.hours, limit=50000)
    if not all_messages:
        async def empty():
            yield f"data: {json.dumps({'chunk': f'最近 {req.hours} 小时没有聊天记录。', 'session_id': ''}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"

        return StreamingResponse(empty(), media_type="text/event-stream")

    context = build_global_context(all_messages)
    session_id = await db.create_ai_session(talker="__global__", title=f"全部聊天总结 ({req.hours}h)")
    user_msg = req.message or f"请总结我最近 {req.hours} 小时内所有微信聊天的内容。"
    ai_messages = [{"role": "user", "content": f"{context}\n\n{user_msg}"}]

    async def generate():
        full_response = ""
        try:
            async for chunk in provider.chat_stream(ai_messages, system_prompt=GLOBAL_SUMMARY_SYSTEM_PROMPT):
                full_response += chunk
                yield f"data: {json.dumps({'chunk': chunk, 'session_id': session_id}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.error(f"Global summary error: {exc}")
            full_response = fallback_global_summary(all_messages, req.hours, exc)
            yield f"data: {json.dumps({'chunk': full_response, 'session_id': session_id}, ensure_ascii=False)}\n\n"

        await db.save_ai_message(session_id, "user", user_msg)
        await db.save_ai_message(session_id, "assistant", full_response)
        yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/sessions")
async def get_ai_sessions(talker: str = ""):
    db = await get_db()
    return await db.get_ai_sessions(talker)


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    db = await get_db()
    return await db.get_ai_messages(session_id)
