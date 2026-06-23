from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.agent.router import AgentRouter
from app.agent.service import wechat_agent_service
from app.scheduler.daily_summary import daily_summary_scheduler

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentConfigRequest(BaseModel):
    enabled: bool | None = None
    entry_name: str | None = None
    entry_talker: str | None = None


class BindRequest(BaseModel):
    entry_name: str = ""


class AgentChatRequest(BaseModel):
    message: str


class DailySummaryConfigRequest(BaseModel):
    enabled: bool | None = None
    receiver: str | None = None
    time: str | None = None
    hours: int | None = None


@router.get("/status")
async def status():
    return await wechat_agent_service.status()


@router.post("/config")
async def configure(req: AgentConfigRequest):
    return await wechat_agent_service.configure(
        enabled=req.enabled,
        entry_name=req.entry_name,
        entry_talker=req.entry_talker,
    )


@router.post("/bind")
async def bind_entry(req: BindRequest):
    return await wechat_agent_service.bind_entry(req.entry_name or None)


@router.post("/chat")
async def chat(req: AgentChatRequest):
    return await wechat_agent_service.handle_entry_text(req.message)


@router.post("/route")
async def route(req: AgentChatRequest):
    decision = await AgentRouter().decide(req.message)
    return decision.to_dict()


@router.post("/actions/{action_id}/confirm")
async def confirm_action(action_id: str):
    return await wechat_agent_service.confirm_action(action_id)


@router.post("/actions/{action_id}/cancel")
async def cancel_action(action_id: str):
    return await wechat_agent_service.cancel_action(action_id)


@router.post("/process")
async def process_now():
    return await wechat_agent_service.process_entry_messages(force=True)


@router.get("/daily-summary/status")
async def daily_summary_status():
    return await daily_summary_scheduler.status()


@router.post("/daily-summary/config")
async def daily_summary_config(req: DailySummaryConfigRequest):
    return await daily_summary_scheduler.configure(
        enabled=req.enabled,
        receiver=req.receiver,
        time_value=req.time,
        hours=req.hours,
    )


@router.post("/daily-summary/preview")
async def daily_summary_preview():
    text = await daily_summary_scheduler.generate_summary()
    return {"status": "ok", "length": len(text), "text": text}


@router.post("/daily-summary/run")
async def daily_summary_run():
    return await daily_summary_scheduler.run_once(reason="manual_api")
