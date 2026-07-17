from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.scheduler.daily_summary import daily_summary_scheduler

router = APIRouter(prefix="/share", tags=["share"])


@router.get("/daily/latest", response_class=HTMLResponse)
async def latest_daily_summary(token: str = Query(default="")):
    if not await daily_summary_scheduler.verify_share_token(token):
        raise HTTPException(status_code=403, detail="Invalid or missing share token")

    html_path = daily_summary_scheduler.latest_summary_html_path()
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Daily summary has not been generated yet")

    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )
