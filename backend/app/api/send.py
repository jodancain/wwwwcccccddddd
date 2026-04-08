import asyncio
from fastapi import APIRouter
from pydantic import BaseModel
from loguru import logger

from app.wechat_sender.automator import WeChatAutomator

router = APIRouter(prefix="/send", tags=["send"])

automator = WeChatAutomator()


class SendTextRequest(BaseModel):
    contact_name: str
    content: str


@router.post("/text")
async def send_text(req: SendTextRequest):
    success = await asyncio.to_thread(automator.send_text, req.contact_name, req.content)
    if success:
        return {"status": "sent", "message": f"Message sent to {req.contact_name}"}
    return {"status": "failed", "message": "Failed to send message. Make sure WeChat is running."}
