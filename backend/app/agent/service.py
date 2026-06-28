from __future__ import annotations

import asyncio
from pathlib import Path
import re
import time

from loguru import logger

from app.agent.claude_agent import ClaudeDirectAgent, ClaudeWechatAgent
from app.agent.dev_agent import DEV_AGENT_VERSION, DevAgent, execute_dev_action_payload
from app.agent.router import AgentRouter, ROUTE_DEVELOPMENT, ROUTE_GENERAL
from app.agent.tools import AgentTools
from app.config.settings import get_settings
from app.dependencies import get_db
from app.scheduler.daily_summary import daily_summary_scheduler
from app.wechat_sender.automator import WeChatAutomator


class WechatAgentService:
    ENTRY_TALKER_KEY = "agent.entry_talker"
    ENTRY_NAME_KEY = "agent.entry_name"
    ENABLED_KEY = "agent.enabled"
    LAST_ID_KEY = "agent.last_message_id"

    def __init__(self):
        self.settings = get_settings()
        self.automator = WeChatAutomator()
        self._last_process_at = 0.0
        self._lock = asyncio.Lock()

    async def status(self) -> dict:
        db = await get_db()
        entry_name = await db.get_setting(self.ENTRY_NAME_KEY, self.settings.AGENT_WECHAT_ENTRY_NAME)
        entry_talker = await db.get_setting(self.ENTRY_TALKER_KEY, "")
        enabled = await self.is_enabled()
        pending = await db.list_pending_actions(limit=10)
        smart_router_enabled = await self._get_bool_setting(
            db,
            "agent.smart_router_enabled",
            bool(self.settings.AGENT_SMART_ROUTER_ENABLED),
        )
        dev_workspace = await db.get_setting(
            "agent.dev_workspace",
            self.settings.AGENT_DEV_WORKSPACE or str(Path.cwd()),
        )
        return {
            "enabled": enabled,
            "entry_name": entry_name,
            "entry_talker": entry_talker,
            "bound": bool(entry_talker),
            "transport_mode": "local_polling" if entry_talker else "openclaw_forward",
            "openclaw_forward_ready": enabled and (
                bool(self.settings.ANTHROPIC_API_KEY) or bool(self.settings.OPENAI_API_KEY)
            ),
            "local_polling_ready": bool(entry_talker),
            "permission_mode": self.settings.AGENT_PERMISSION_MODE,
            "ai_provider": self.settings.AI_PROVIDER,
            "openai_base_url": self.settings.OPENAI_BASE_URL,
            "openai_model": self.settings.OPENAI_MODEL,
            "openai_configured": bool(self.settings.OPENAI_API_KEY),
            "claude_configured": bool(self.settings.ANTHROPIC_API_KEY),
            "claude_model": self.settings.CLAUDE_MODEL,
            "dev_mode_enabled": await self._get_bool_setting(
                db,
                "agent.dev_mode_enabled",
                bool(self.settings.AGENT_DEV_MODE_ENABLED),
            ),
            "dev_auto_apply": await self._get_bool_setting(
                db,
                "agent.dev_auto_apply",
                bool(self.settings.AGENT_DEV_AUTO_APPLY),
            ),
            "dev_workspace": dev_workspace,
            "dev_agent_version": DEV_AGENT_VERSION,
            "smart_router_enabled": smart_router_enabled,
            "router_mode": "ai_first" if smart_router_enabled else "local_fallback",
            "claude_code_planner_enabled": await self._get_bool_setting(
                db,
                "agent.claude_code_planner_enabled",
                bool(self.settings.AGENT_DEV_ENABLE_CLAUDE_CODE_TOOL),
            ),
            "claude_code_cli_path": self.settings.CLAUDE_CODE_CLI_PATH or "auto",
            "claude_code_model": self.settings.CLAUDE_CODE_MODEL,
            "daily_summary": await daily_summary_scheduler.status(),
            "pending_actions": pending,
        }

    async def is_enabled(self) -> bool:
        db = await get_db()
        stored = await db.get_setting(self.ENABLED_KEY, "")
        if stored:
            return stored.lower() in {"1", "true", "yes", "on"}
        return bool(self.settings.CLAUDE_AGENT_ENABLED)

    async def _get_bool_setting(self, db, key: str, default: bool) -> bool:
        raw = await db.get_setting(key, "true" if default else "false")
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "开启", "开"}

    async def configure(
        self,
        *,
        enabled: bool | None = None,
        entry_name: str | None = None,
        entry_talker: str | None = None,
        dev_mode_enabled: bool | None = None,
        dev_auto_apply: bool | None = None,
        dev_workspace: str | None = None,
        smart_router_enabled: bool | None = None,
        claude_code_planner_enabled: bool | None = None,
    ) -> dict:
        db = await get_db()
        if enabled is not None:
            await db.set_setting(self.ENABLED_KEY, "true" if enabled else "false", "WeChat agent enabled")
        if entry_name is not None:
            await db.set_setting(self.ENTRY_NAME_KEY, entry_name.strip(), "WeChat agent entry display name")
        if entry_talker is not None:
            await db.set_setting(self.ENTRY_TALKER_KEY, entry_talker.strip(), "WeChat agent entry talker")
        if dev_mode_enabled is not None:
            await db.set_setting(
                "agent.dev_mode_enabled",
                "true" if dev_mode_enabled else "false",
                "Developer agent mode enabled",
            )
        if dev_auto_apply is not None:
            await db.set_setting(
                "agent.dev_auto_apply",
                "true" if dev_auto_apply else "false",
                "Developer agent auto-apply enabled",
            )
        if dev_workspace is not None:
            await db.set_setting("agent.dev_workspace", dev_workspace.strip(), "Developer agent workspace")
        if smart_router_enabled is not None:
            await db.set_setting(
                "agent.smart_router_enabled",
                "true" if smart_router_enabled else "false",
                "Smart semantic router enabled",
            )
        if claude_code_planner_enabled is not None:
            await db.set_setting(
                "agent.claude_code_planner_enabled",
                "true" if claude_code_planner_enabled else "false",
                "Claude Code planner enabled",
            )
        return await self.status()

    async def bind_entry(self, name: str | None = None) -> dict:
        db = await get_db()
        entry_name = (name or await db.get_setting(self.ENTRY_NAME_KEY, self.settings.AGENT_WECHAT_ENTRY_NAME)).strip()
        await db.set_setting(self.ENTRY_NAME_KEY, entry_name, "WeChat agent entry display name")
        matches = await db.search_conversations(entry_name, limit=8)
        if len(matches) == 1:
            talker = matches[0]["talker"]
            await db.set_setting(self.ENTRY_TALKER_KEY, talker, "WeChat agent entry talker")
            latest = await db.get_recent_messages_for_talker(talker, limit=1)
            if latest:
                await db.set_setting(self.LAST_ID_KEY, str(latest[-1].get("id") or 0), "Last processed agent entry message id")
            await db.add_agent_audit("bind_entry", {"entry_name": entry_name, "talker": talker})
            return {"status": "bound", "entry_name": entry_name, "entry_talker": talker, "matches": matches}
        return {
            "status": "not_bound" if not matches else "ambiguous",
            "entry_name": entry_name,
            "matches": matches,
            "message": "没有找到匹配会话" if not matches else "找到多个匹配会话，请指定 entry_talker",
        }

    async def handle_entry_text(self, text: str) -> dict:
        db = await get_db()
        stripped_text = text.strip()
        if stripped_text.startswith("确认 "):
            return await self.confirm_action(stripped_text.split(None, 1)[1].strip())
        confirm = re.match(r"^\s*确认\s+([a-zA-Z0-9_-]{4,})\s*$", text)
        if confirm:
            return await self.confirm_action(confirm.group(1))

        ascii_confirm = re.match(r"^\s*confirm\s+([a-zA-Z0-9_-]{4,})\s*$", text, flags=re.IGNORECASE)
        if ascii_confirm:
            return await self.confirm_action(ascii_confirm.group(1))

        daily_summary_result = await self._handle_daily_summary_control(text)
        if daily_summary_result:
            return daily_summary_result

        health_result = await self._handle_agent_health_question(text)
        if health_result:
            return health_result

        route = await AgentRouter().decide(text)
        if route.route == ROUTE_DEVELOPMENT:
            agent = DevAgent(db)
            reply = await agent.reply(text)
            await db.add_agent_audit(
                "dev_agent_reply",
                {
                    "input": text,
                    "route": route.to_dict(),
                    "reply": reply.text,
                    "used_claude": reply.used_claude,
                    "pending_actions": reply.pending_actions or [],
                },
            )
            return {
                "status": "ok",
                "reply": reply.text,
                "used_claude": reply.used_claude,
                "agent_route": route.to_dict(),
                "pending_actions": reply.pending_actions or [],
            }

        if route.route == ROUTE_GENERAL:
            agent = ClaudeDirectAgent()
            reply = await agent.reply(text)
            await db.add_agent_audit(
                "direct_agent_reply",
                {"input": text, "route": route.to_dict(), "reply": reply.text, "used_claude": reply.used_claude},
            )
            return {
                "status": "ok",
                "reply": reply.text,
                "used_claude": reply.used_claude,
                "agent_route": route.to_dict(),
            }

        agent = ClaudeWechatAgent(AgentTools(db))
        reply = await agent.reply(text)
        await db.add_agent_audit(
            "agent_reply",
            {"input": text, "route": route.to_dict(), "reply": reply.text, "used_claude": reply.used_claude},
        )
        return {
            "status": "ok",
            "reply": reply.text,
            "used_claude": reply.used_claude,
            "agent_route": route.to_dict(),
            "pending_action": reply.pending_action,
        }

    async def _handle_agent_health_question(self, text: str) -> dict | None:
        compact = re.sub(r"\s+", "", text.lower())
        health_terms = (
            "用不了",
            "不能用",
            "没反应",
            "没回复",
            "不回复",
            "又挂",
            "挂了",
            "恢复了吗",
            "服务恢复",
            "为什么又",
        )
        explicit_dev_terms = (
            "/dev",
            "dev:",
            "codex:",
            "claude code:",
            "修复",
            "改代码",
            "修改代码",
            "新增",
            "加功能",
            "优化",
        )
        if not any(term in compact for term in health_terms):
            return None
        if any(term in compact for term in explicit_dev_terms):
            return None

        status = await self.status()
        daily = status.get("daily_summary") or {}
        model = status.get("openai_model") if status.get("ai_provider") == "openai" else status.get("claude_model")
        reply = (
            "我这边服务是在线的。\n"
            f"- 微信入口：{status.get('transport_mode')}，可用：{status.get('openclaw_forward_ready')}\n"
            f"- 当前模型：{status.get('ai_provider')} / {model}\n"
            f"- 聊天记录同步：后端正常，最近每日总结状态：{daily.get('last_status') or '暂无'}\n\n"
            "刚才“为什么又用不了”这类话容易被当成开发修复任务；现在我会先按状态询问处理。"
            "普通聊天可以直接问，想强制普通对话也可以用 `/chat 你的问题`。"
        )
        return {
            "status": "ok",
            "reply": reply,
            "agent_route": {"route": "health", "confidence": 1.0, "reason": "agent health/status question", "method": "local"},
        }

    async def _handle_daily_summary_control(self, text: str) -> dict | None:
        compact = re.sub(r"\s+", "", text)
        if not any(word in compact for word in ("总结", "重点", "日报", "复盘")):
            return None
        if not any(word in compact for word in ("每天", "每日", "定时", "自动", "现在", "立即", "发一次", "状态", "开启", "关闭", "取消", "停止", "预览")):
            return None

        time_value = self._extract_daily_summary_time(text)
        hours = self._extract_daily_summary_hours(text)

        if any(word in compact for word in ("关闭", "停止", "取消", "别发", "不要发")):
            status = await daily_summary_scheduler.configure(enabled=False)
            return {
                "status": "ok",
                "reply": "已关闭每日微信总结自动发送。",
                "agent_route": {"route": "daily_summary", "confidence": 1.0, "reason": "disable daily summary", "method": "local"},
                "daily_summary": status,
            }

        if any(word in compact for word in ("状态", "设置", "开了吗", "开启了吗")) and not any(word in compact for word in ("开启", "打开", "启用")):
            status = await daily_summary_scheduler.status()
            enabled = "开启" if status.get("enabled") else "关闭"
            reply = (
                f"每日微信总结当前：{enabled}\n"
                f"时间：{status.get('time')}\n"
                f"接收人：{status.get('receiver')}\n"
                f"范围：最近 {status.get('hours')} 小时\n"
                f"下次运行：{status.get('next_run_at') or '未安排'}"
            )
            return {
                "status": "ok",
                "reply": reply,
                "agent_route": {"route": "daily_summary", "confidence": 1.0, "reason": "daily summary status", "method": "local"},
                "daily_summary": status,
            }

        if any(word in compact for word in ("预览", "看看效果", "生成一下")):
            preview = await daily_summary_scheduler.generate_summary()
            return {
                "status": "ok",
                "reply": preview,
                "agent_route": {"route": "daily_summary", "confidence": 1.0, "reason": "daily summary preview", "method": "local"},
            }

        manual_send = any(word in compact for word in ("现在", "立即", "马上", "发一次", "来一份"))
        automation_intent = any(word in compact for word in ("以后", "自动", "每天", "定时")) and not manual_send
        if manual_send and not automation_intent:
            result = await daily_summary_scheduler.run_once(reason="wechat_agent_manual")
            sent = "已发送" if result.get("sent") else "发送失败"
            return {
                "status": result.get("status", "ok"),
                "reply": f"{sent}每日微信总结给 {result.get('receiver', '默认接收人')}。",
                "agent_route": {"route": "daily_summary", "confidence": 1.0, "reason": "manual daily summary send", "method": "local"},
                "daily_summary_result": result,
            }

        if any(word in compact for word in ("开启", "打开", "启用", "以后", "每天", "每日", "定时", "自动")):
            status = await daily_summary_scheduler.configure(
                enabled=True,
                time_value=time_value,
                hours=hours,
            )
            reply = (
                "已开启每日微信总结自动发送。\n"
                f"时间：{status.get('time')}\n"
                f"接收人：{status.get('receiver')}\n"
                f"范围：最近 {status.get('hours')} 小时\n"
                "你也可以说：现在发一次每日总结 / 关闭每日总结 / 每日总结状态。"
            )
            return {
                "status": "ok",
                "reply": reply,
                "agent_route": {"route": "daily_summary", "confidence": 1.0, "reason": "enable daily summary", "method": "local"},
                "daily_summary": status,
            }

        return None

    def _extract_daily_summary_time(self, text: str) -> str | None:
        match = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", text)
        if match:
            return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"
        match = re.search(r"(\d{1,2})\s*点(?:\s*(\d{1,2})\s*分?)?", text)
        if match:
            minute = int(match.group(2) or 0)
            return f"{int(match.group(1)):02d}:{minute:02d}"
        if "早上" in text or "上午" in text:
            return "09:00"
        if "晚上" in text or "睡前" in text:
            return "21:00"
        return None

    def _extract_daily_summary_hours(self, text: str) -> int | None:
        match = re.search(r"最近\s*(\d{1,3})\s*(小时|天)", text)
        if not match:
            return None
        amount = int(match.group(1))
        return amount if match.group(2) == "小时" else amount * 24

    async def confirm_action(self, action_id: str) -> dict:
        db = await get_db()
        action = await db.get_pending_action(action_id)
        if not action or action.get("status") != "pending":
            return {"status": "not_found", "reply": f"没有找到待确认动作 {action_id}，可能已经处理过了。"}
        payload = action.get("payload") or {}
        if action.get("action_type") in {"dev_write_files", "dev_apply_patch", "dev_shell_command"}:
            try:
                result = await execute_dev_action_payload(action.get("action_type"), payload)
                await db.update_pending_action_status(action_id, "confirmed")
                await db.add_agent_audit("confirm_dev_action", {"action_id": action_id, "result": result})
                return {
                    "status": "executed",
                    "reply": f"已执行开发动作 {action_id}：\n{result}",
                    "result": result,
                }
            except Exception as exc:  # noqa: BLE001
                await db.update_pending_action_status(action_id, "failed")
                await db.add_agent_audit("confirm_dev_action_failed", {"action_id": action_id, "error": str(exc)})
                return {"status": "failed", "reply": f"开发动作 {action_id} 执行失败：{exc}"}

        if action.get("action_type") != "send_wechat_text":
            return {"status": "unsupported", "reply": f"动作 {action_id} 暂不支持执行。"}

        contact_name = str(payload.get("contact_name") or "").strip()
        content = str(payload.get("content") or "").strip()
        if not contact_name or not content:
            await db.update_pending_action_status(action_id, "failed")
            return {"status": "failed", "reply": f"动作 {action_id} 缺少收件人或内容，已取消。"}

        success = await asyncio.to_thread(self.automator.send_text, contact_name, content)
        await db.update_pending_action_status(action_id, "confirmed" if success else "failed")
        await db.add_agent_audit(
            "confirm_action",
            {"action_id": action_id, "contact_name": contact_name, "success": success},
        )
        if success:
            return {"status": "sent", "reply": f"已确认并发送给 {contact_name}。"}
        return {"status": "failed", "reply": "发送失败，请确认微信正在运行且联系人名称能被搜索到。"}

    async def cancel_action(self, action_id: str) -> dict:
        db = await get_db()
        updated = await db.update_pending_action_status(action_id, "canceled")
        await db.add_agent_audit("cancel_action", {"action_id": action_id, "updated": updated})
        if updated:
            return {"status": "canceled", "reply": f"已取消待确认动作 {action_id}。"}
        return {"status": "not_found", "reply": f"没有找到可取消的待确认动作 {action_id}。"}

    async def process_entry_messages(self, force: bool = False) -> dict:
        if not force and time.time() - self._last_process_at < max(1, self.settings.AGENT_POLL_SECONDS):
            return {"status": "skipped", "reason": "rate_limited"}
        async with self._lock:
            self._last_process_at = time.time()
            if not await self.is_enabled():
                return {"status": "disabled"}
            db = await get_db()
            entry_talker = await db.get_setting(self.ENTRY_TALKER_KEY, "")
            entry_name = await db.get_setting(self.ENTRY_NAME_KEY, self.settings.AGENT_WECHAT_ENTRY_NAME)
            if not entry_talker:
                bind = await self.bind_entry(entry_name)
                if bind.get("status") != "bound":
                    return bind
                entry_talker = bind["entry_talker"]

            last_id = int(await db.get_setting(self.LAST_ID_KEY, "0") or 0)
            messages = await db.get_new_inbound_messages(entry_talker, last_id, limit=5)
            if not messages:
                return {"status": "idle", "processed": 0}

            processed = 0
            for item in messages:
                msg_id = int(item.get("id") or 0)
                text = (item.get("content") or item.get("display_content") or "").strip()
                if not text:
                    await db.set_setting(self.LAST_ID_KEY, str(msg_id), "Last processed agent entry message id")
                    continue
                result = await self.handle_entry_text(text)
                reply = result.get("reply") or "我处理完了，但没有生成回复。"
                sent = await asyncio.to_thread(self.automator.send_text, entry_name, reply)
                await db.add_agent_audit(
                    "entry_message_processed",
                    {"message_id": msg_id, "sent": sent, "status": result.get("status")},
                )
                await db.set_setting(self.LAST_ID_KEY, str(msg_id), "Last processed agent entry message id")
                processed += 1
            return {"status": "ok", "processed": processed}


wechat_agent_service = WechatAgentService()
