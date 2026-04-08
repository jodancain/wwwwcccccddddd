import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from loguru import logger

from app.dependencies import get_db
from app.config.settings import get_settings
from app.ai.provider_base import AIProvider
from app.ai.gemini_provider import GeminiProvider
from app.ai.openai_provider import OpenAIProvider
from app.ai.context_builder import (
    SYSTEM_PROMPT,
    SUGGEST_REPLY_PROMPT,
    GLOBAL_SUMMARY_SYSTEM_PROMPT,
    build_conversation_context,
    build_global_context,
)

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


class SuggestRequest(BaseModel):
    talker: str


@router.post("/chat")
async def ai_chat(req: ChatRequest):
    db = await get_db()
    provider = get_ai_provider()

    # Get or create session
    session_id = req.session_id
    if not session_id:
        session_id = await db.create_ai_session(talker=req.talker)

    # Build context from conversation
    context = ""
    if req.talker:
        messages = await db.get_all_messages_for_talker(req.talker)
        if messages:
            talker_name = messages[0].get("remark") or messages[0].get("nickname") or req.talker
            is_group = bool(messages[0].get("is_group"))
            context = build_conversation_context(messages, talker_name, is_group)

    # Build AI message history
    ai_history = await db.get_ai_messages(session_id)
    ai_messages = []
    if context:
        ai_messages.append({"role": "user", "content": f"以下是当前微信对话上下文：\n\n{context}"})
        ai_messages.append({"role": "assistant", "content": "好的，我已经看到了这段对话内容。请问有什么需要我帮忙的？"})

    for msg in ai_history:
        ai_messages.append({"role": msg["role"], "content": msg["content"]})

    ai_messages.append({"role": "user", "content": req.message})

    # Call AI
    response = await provider.chat(ai_messages, system_prompt=SYSTEM_PROMPT)

    # Save messages
    await db.save_ai_message(session_id, "user", req.message)
    await db.save_ai_message(session_id, "assistant", response)

    return {
        "session_id": session_id,
        "response": response,
    }


@router.post("/chat/stream")
async def ai_chat_stream(req: ChatRequest):
    db = await get_db()
    provider = get_ai_provider()

    session_id = req.session_id
    if not session_id:
        session_id = await db.create_ai_session(talker=req.talker)

    # Build context
    context = ""
    if req.talker:
        messages = await db.get_all_messages_for_talker(req.talker)
        if messages:
            talker_name = messages[0].get("remark") or messages[0].get("nickname") or req.talker
            is_group = bool(messages[0].get("is_group"))
            context = build_conversation_context(messages, talker_name, is_group)

    ai_history = await db.get_ai_messages(session_id)
    ai_messages = []
    if context:
        ai_messages.append({"role": "user", "content": f"以下是当前微信对话上下文：\n\n{context}"})
        ai_messages.append({"role": "assistant", "content": "好的，我已经看到了这段对话内容。请问有什么需要我帮忙的？"})

    for msg in ai_history:
        ai_messages.append({"role": msg["role"], "content": msg["content"]})

    ai_messages.append({"role": "user", "content": req.message})

    async def generate():
        full_response = ""
        try:
            async for chunk in provider.chat_stream(ai_messages, system_prompt=SYSTEM_PROMPT):
                full_response += chunk
                yield f"data: {json.dumps({'chunk': chunk, 'session_id': session_id}, ensure_ascii=False)}\n\n"

            # Save after streaming completes
            await db.save_ai_message(session_id, "user", req.message)
            await db.save_ai_message(session_id, "assistant", full_response)

            yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/suggest-replies")
async def suggest_replies(req: SuggestRequest):
    db = await get_db()
    provider = get_ai_provider()

    messages = await db.get_all_messages_for_talker(req.talker)
    if not messages:
        return {"replies": ["你好！", "好的，收到", "我稍后回复你"]}

    talker_name = messages[0].get("remark") or messages[0].get("nickname") or req.talker
    is_group = bool(messages[0].get("is_group"))
    context = build_conversation_context(messages, talker_name, is_group)

    ai_messages = [
        {"role": "user", "content": f"{context}\n\n{SUGGEST_REPLY_PROMPT}"}
    ]

    try:
        response = await provider.chat(ai_messages, system_prompt="你是一个微信回复建议生成器。只输出JSON数组格式的回复建议。")
        # Parse JSON array from response
        text = response.strip()
        # Try to extract JSON array
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            replies = json.loads(text[start:end])
            return {"replies": replies[:3]}
    except Exception as e:
        logger.error(f"Suggest replies error: {e}")

    return {"replies": ["好的", "收到，谢谢", "我知道了"]}


class GlobalSummaryRequest(BaseModel):
    hours: int = 24  # Summarize last N hours
    message: str = ""  # Optional custom question


@router.post("/global-summary/stream")
async def global_summary_stream(req: GlobalSummaryRequest):
    """Summarize ALL recent chats across all conversations."""
    db = await get_db()
    provider = get_ai_provider()

    # Load all recent messages
    all_messages = await db.get_all_recent_messages(hours=req.hours, limit=8000)

    if not all_messages:
        async def empty():
            yield f"data: {json.dumps({'chunk': '最近没有聊天记录。', 'session_id': ''}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
        return StreamingResponse(empty(), media_type="text/event-stream")

    context = build_global_context(all_messages)

    # Create a session for this global summary
    session_id = await db.create_ai_session(talker="__global__", title=f"全部聊天总结 ({req.hours}h)")

    user_msg = req.message or f"请总结我最近 {req.hours} 小时内所有微信聊天的内容。"
    ai_messages = [
        {"role": "user", "content": f"{context}\n\n{user_msg}"},
    ]

    async def generate():
        full_response = ""
        try:
            async for chunk in provider.chat_stream(ai_messages, system_prompt=GLOBAL_SUMMARY_SYSTEM_PROMPT):
                full_response += chunk
                yield f"data: {json.dumps({'chunk': chunk, 'session_id': session_id}, ensure_ascii=False)}\n\n"

            await db.save_ai_message(session_id, "user", user_msg)
            await db.save_ai_message(session_id, "assistant", full_response)

            yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"Global summary error: {e}")
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/sessions")
async def get_ai_sessions(talker: str = ""):
    db = await get_db()
    return await db.get_ai_sessions(talker)


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    db = await get_db()
    return await db.get_ai_messages(session_id)
