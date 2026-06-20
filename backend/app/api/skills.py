import json
import re

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from app.api.ai_chat import get_ai_provider
from app.ai.fallback import provider_error_message
from app.dependencies import get_db
from app.skills.generator import fallback_skill_from_chat, generate_skill_from_chat
from app.skills.manager import delete_skill, get_skill, import_skill_from_url, list_skills, save_skill

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
    return list_skills()


@router.get("/{slug}")
async def api_get_skill(slug: str):
    skill = get_skill(slug)
    if not skill:
        return {"error": "Skill not found"}
    return skill


@router.post("/save")
async def api_save_skill(req: SaveSkillRequest):
    return save_skill(req.slug, req.content)


@router.delete("/{slug}")
async def api_delete_skill(slug: str):
    return {"success": delete_skill(slug)}


@router.post("/import")
async def api_import_skill(req: ImportSkillRequest):
    try:
        return import_skill_from_url(req.url)
    except Exception as e:
        logger.error(f"Import skill failed: {e}")
        return {"error": str(e)}


@router.post("/generate/stream")
async def api_generate_skill_stream(req: GenerateSkillRequest):
    db = await get_db()
    provider = get_ai_provider()
    messages = await db.get_all_messages_for_talker(req.talker, max_messages=50000)

    if not messages:
        async def empty():
            yield f"data: {json.dumps({'error': '没有找到聊天记录'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(empty(), media_type="text/event-stream")

    talker_name = messages[0].get("remark") or messages[0].get("nickname") or req.talker
    is_group = bool(messages[0].get("is_group", 0))

    async def generate():
        try:
            content = await generate_skill_from_chat(provider, messages, talker_name, is_group)
        except Exception as e:
            logger.error(f"Generate skill error: {e}")
            notice = provider_error_message(e)
            content = fallback_skill_from_chat(messages, talker_name, is_group)
            notice_chunk = f"{notice}\n\n"
            yield f"data: {json.dumps({'chunk': notice_chunk}, ensure_ascii=False)}\n\n"

        slug = re.sub(r"[^\w\u4e00-\u9fff-]", "-", talker_name.lower()).strip("-") or "unnamed"
        saved = save_skill(slug, content)
        yield f"data: {json.dumps({'chunk': content}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'done': True, 'slug': saved['slug'], 'name': talker_name}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
