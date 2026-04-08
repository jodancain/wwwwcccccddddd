import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from loguru import logger

from app.dependencies import get_db
from app.api.ai_chat import get_ai_provider
from app.skills.manager import (
    list_skills, get_skill, save_skill, delete_skill,
    import_skill_from_url, build_skill_system_prompt,
)
from app.skills.generator import generate_skill_from_chat

router = APIRouter(prefix="/skills", tags=["skills"])


class SaveSkillRequest(BaseModel):
    slug: str
    content: str


class ImportSkillRequest(BaseModel):
    url: str


class GenerateSkillRequest(BaseModel):
    talker: str


@router.get("/")
async def api_list_skills():
    """List all available skills."""
    return list_skills()


@router.get("/{slug}")
async def api_get_skill(slug: str):
    """Get a skill by slug."""
    skill = get_skill(slug)
    if not skill:
        return {"error": "Skill not found"}
    return skill


@router.post("/save")
async def api_save_skill(req: SaveSkillRequest):
    """Save or update a skill."""
    return save_skill(req.slug, req.content)


@router.delete("/{slug}")
async def api_delete_skill(slug: str):
    """Delete a skill."""
    ok = delete_skill(slug)
    return {"success": ok}


@router.post("/import")
async def api_import_skill(req: ImportSkillRequest):
    """Import a skill from a GitHub URL."""
    try:
        return import_skill_from_url(req.url)
    except Exception as e:
        logger.error(f"Import skill failed: {e}")
        return {"error": str(e)}


@router.post("/generate/stream")
async def api_generate_skill_stream(req: GenerateSkillRequest):
    """Generate a persona skill from chat history (streaming)."""
    db = await get_db()
    provider = get_ai_provider()

    # Load all messages for this talker
    messages = await db.get_all_messages_for_talker(req.talker, max_messages=50000)
    if not messages:
        async def empty():
            yield f"data: {json.dumps({'error': '没有找到聊天记录'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(empty(), media_type="text/event-stream")

    talker_name = messages[0].get("remark") or messages[0].get("nickname") or req.talker
    is_group = bool(messages[0].get("is_group", 0))

    async def generate():
        try:
            # Use streaming for large content
            from app.ai.context_builder import _format_messages
            from app.skills.generator import GENERATE_SKILL_PROMPT
            from datetime import datetime

            # Build context
            lines = [f"以下是「{talker_name}」的微信聊天记录（共{len(messages)}条消息）：\n"]
            for msg in messages:
                ts = msg.get("create_time", 0)
                time_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
                content = msg.get("content", "")
                if not content or content.startswith("["):
                    continue
                is_sender = msg.get("is_sender", 0)
                if is_sender:
                    d = "[我]"
                elif is_group and msg.get("sender"):
                    d = f"[{msg['sender']}]"
                else:
                    d = f"[{talker_name}]"
                lines.append(f"{time_str} {d} {content}")

            chat_context = "\n".join(lines)
            if len(chat_context) > 400000:
                chat_context = chat_context[-400000:]

            user_msg = f"{chat_context}\n\n请根据以上聊天记录，为「{talker_name}」生成一个完整的 SKILL.md 人物画像文件。"
            ai_messages = [{"role": "user", "content": user_msg}]

            full_response = ""
            async for chunk in provider.chat_stream(ai_messages, system_prompt=GENERATE_SKILL_PROMPT):
                full_response += chunk
                yield f"data: {json.dumps({'chunk': chunk}, ensure_ascii=False)}\n\n"

            # Auto-save the generated skill
            import re
            content = full_response.strip()
            if content.startswith("```"):
                content = re.sub(r"^```\w*\n", "", content)
                content = re.sub(r"\n```\s*$", "", content)
            if not content.startswith("---"):
                content = f"---\nname: {talker_name}\ndescription: \"基于微信聊天记录生成的人物画像\"\nversion: \"1.0.0\"\n---\n\n{content}"

            slug = re.sub(r"[^\w\u4e00-\u9fff-]", "-", talker_name.lower()).strip("-") or "unnamed"
            saved = save_skill(slug, content)

            yield f"data: {json.dumps({'done': True, 'slug': saved['slug'], 'name': talker_name}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"Generate skill error: {e}")
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
