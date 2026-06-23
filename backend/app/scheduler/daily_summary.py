from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger

from app.ai.context_builder import GLOBAL_SUMMARY_SYSTEM_PROMPT, build_global_context
from app.ai.fallback import fallback_global_summary
from app.ai.gemini_provider import GeminiProvider
from app.ai.openai_provider import OpenAIProvider
from app.ai.provider_base import AIProvider
from app.config.settings import get_settings
from app.dependencies import get_db
from app.wechat_sender.automator import WeChatAutomator


@dataclass
class DailySummaryConfig:
    enabled: bool
    receiver: str
    time: str
    hours: int
    max_messages: int


@dataclass
class DailySummaryStatus:
    enabled: bool
    receiver: str
    time: str
    hours: int
    max_messages: int
    running: bool
    last_run_at: str = ""
    last_status: str = ""
    last_error: str = ""
    next_run_at: str = ""

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "receiver": self.receiver,
            "time": self.time,
            "hours": self.hours,
            "max_messages": self.max_messages,
            "running": self.running,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "next_run_at": self.next_run_at,
        }


class DailySummaryScheduler:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.automator = WeChatAutomator()
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._running = False
        self._last_run_at = ""
        self._last_status = ""
        self._last_error = ""
        self._next_run_at = ""
        self._send_lock = asyncio.Lock()

    async def status(self) -> dict:
        config = await self.get_config()
        self._refresh_next_run_at(config)
        return DailySummaryStatus(
            enabled=config.enabled,
            receiver=config.receiver,
            time=config.time,
            hours=config.hours,
            max_messages=config.max_messages,
            running=self._running,
            last_run_at=self._last_run_at,
            last_status=self._last_status,
            last_error=self._last_error,
            next_run_at=self._next_run_at,
        ).to_dict()

    async def start(self) -> None:
        self._running = True
        logger.info("Daily summary scheduler started")
        try:
            while not self._stop_event.is_set():
                config = await self.get_config()
                if not config.enabled:
                    self._refresh_next_run_at(config)
                    wait_result = await self._wait_for_change_or_stop(60)
                    if wait_result == "stop":
                        break
                    continue

                next_run = self._next_run_time(datetime.now(), config.time)
                self._next_run_at = next_run.isoformat(timespec="seconds")
                wait_seconds = max(1.0, (next_run - datetime.now()).total_seconds())
                wait_result = await self._wait_for_change_or_stop(wait_seconds)
                if wait_result == "stop":
                    break
                if wait_result == "timeout":
                    await self.run_once(reason="schedule")
        finally:
            self._running = False
            logger.info("Daily summary scheduler stopped")

    def stop(self) -> None:
        self._stop_event.set()

    async def get_config(self) -> DailySummaryConfig:
        db = await get_db()
        enabled_raw = await db.get_setting(
            "daily_summary.enabled",
            "true" if self.settings.DAILY_SUMMARY_ENABLED else "false",
        )
        receiver = await db.get_setting("daily_summary.receiver", self.settings.DAILY_SUMMARY_RECEIVER)
        time_value = await db.get_setting("daily_summary.time", self.settings.DAILY_SUMMARY_TIME)
        hours_raw = await db.get_setting("daily_summary.hours", str(self.settings.DAILY_SUMMARY_HOURS))
        max_messages_raw = await db.get_setting(
            "daily_summary.max_messages",
            str(self.settings.DAILY_SUMMARY_MAX_MESSAGES),
        )
        return DailySummaryConfig(
            enabled=str(enabled_raw).strip().lower() in {"1", "true", "yes", "on", "开启", "开"},
            receiver=(receiver or self.settings.DAILY_SUMMARY_RECEIVER).strip() or "文件传输助手",
            time=self._normalize_time(time_value),
            hours=self._parse_int(hours_raw, self.settings.DAILY_SUMMARY_HOURS, minimum=1, maximum=720),
            max_messages=self._parse_int(
                max_messages_raw,
                self.settings.DAILY_SUMMARY_MAX_MESSAGES,
                minimum=1,
                maximum=200000,
            ),
        )

    async def configure(
        self,
        *,
        enabled: bool | None = None,
        receiver: str | None = None,
        time_value: str | None = None,
        hours: int | None = None,
    ) -> dict:
        db = await get_db()
        if enabled is not None:
            await db.set_setting("daily_summary.enabled", "true" if enabled else "false", "Daily summary enabled")
        if receiver is not None and receiver.strip():
            await db.set_setting("daily_summary.receiver", receiver.strip(), "Daily summary receiver")
        if time_value is not None and time_value.strip():
            await db.set_setting("daily_summary.time", self._normalize_time(time_value), "Daily summary time")
        if hours is not None:
            safe_hours = self._parse_int(str(hours), 24, minimum=1, maximum=720)
            await db.set_setting("daily_summary.hours", str(safe_hours), "Daily summary hours")
        config = await self.get_config()
        self._refresh_next_run_at(config)
        self._wake_event.set()
        return await self.status()

    async def run_once(self, reason: str = "manual") -> dict:
        async with self._send_lock:
            now = datetime.now().isoformat(timespec="seconds")
            self._last_run_at = now
            self._last_error = ""
            db = await get_db()
            try:
                config = await self.get_config()
                text = await self.generate_summary(config=config)
                sent = await asyncio.to_thread(
                    self.automator.send_text,
                    config.receiver,
                    text,
                )
                self._last_status = "sent" if sent else "send_failed"
                result = {
                    "status": self._last_status,
                    "reason": reason,
                    "receiver": config.receiver,
                    "length": len(text),
                    "sent": sent,
                }
                await db.add_agent_audit("daily_summary_run", result)
                return result
            except Exception as exc:  # noqa: BLE001
                self._last_status = "failed"
                self._last_error = str(exc)
                result = {"status": "failed", "reason": reason, "error": str(exc)}
                await db.add_agent_audit("daily_summary_failed", result)
                logger.exception(f"Daily summary failed: {exc}")
                return result

    async def generate_summary(self, config: DailySummaryConfig | None = None) -> str:
        config = config or await self.get_config()
        db = await get_db()
        hours = config.hours
        messages = await db.get_all_recent_messages(
            hours=hours,
            limit=config.max_messages,
        )
        if not messages:
            return f"最近 {hours} 小时没有同步到新的微信聊天记录。"

        provider = self._get_provider()
        context = build_global_context(messages)
        user_msg = (
            f"请总结最近 {hours} 小时所有微信聊天记录。"
            "输出要适合直接发到微信：先给一句话总览，然后列出重要对话、待办、风险和需要我回复的人。"
        )
        ai_messages = [{"role": "user", "content": f"{context}\n\n{user_msg}"}]
        try:
            summary = await provider.chat(ai_messages, system_prompt=GLOBAL_SUMMARY_SYSTEM_PROMPT)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Daily summary AI generation failed: {exc}")
            summary = fallback_global_summary(messages, hours, exc)

        header = f"每日微信总结｜最近 {hours} 小时"
        body = summary.strip() or "今天没有生成有效总结。"
        return f"{header}\n\n{body}"

    def _get_provider(self) -> AIProvider:
        settings = get_settings()
        if settings.AI_PROVIDER == "openai":
            return OpenAIProvider()
        return GeminiProvider()

    def _next_run_time(self, now: datetime, time_value: str) -> datetime:
        hour, minute = self._parse_time(time_value)
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _refresh_next_run_at(self, config: DailySummaryConfig) -> None:
        if not config.enabled:
            self._next_run_at = ""
            return
        self._next_run_at = self._next_run_time(datetime.now(), config.time).isoformat(timespec="seconds")

    async def _wait_for_change_or_stop(self, timeout: float) -> str:
        stop_task = asyncio.create_task(self._stop_event.wait())
        wake_task = asyncio.create_task(self._wake_event.wait())
        done, pending = await asyncio.wait(
            {stop_task, wake_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if not done:
            return "timeout"
        if stop_task in done and stop_task.result():
            return "stop"
        if wake_task in done and wake_task.result():
            self._wake_event.clear()
            return "wake"
        return "timeout"

    def _parse_time(self, value: str) -> tuple[int, int]:
        try:
            hour_s, minute_s = (value or "09:00").strip().split(":", 1)
            hour = min(23, max(0, int(hour_s)))
            minute = min(59, max(0, int(minute_s)))
            return hour, minute
        except Exception:  # noqa: BLE001
            logger.warning(f"Invalid DAILY_SUMMARY_TIME={value!r}; using 09:00")
            return 9, 0

    def _normalize_time(self, value: str) -> str:
        hour, minute = self._parse_time(value)
        return f"{hour:02d}:{minute:02d}"

    def _parse_int(self, value: str, default: int, *, minimum: int, maximum: int) -> int:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            parsed = default
        return min(maximum, max(minimum, parsed))


daily_summary_scheduler = DailySummaryScheduler()
