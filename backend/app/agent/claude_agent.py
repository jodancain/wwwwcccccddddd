from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.agent.tools import AgentTools
from app.config.settings import get_settings


SYSTEM_PROMPT = """你是用户的本地微信聊天记录 agent。
你可以帮助用户查询、总结和分析已经解密并同步到 WeChatAI 本地数据库里的微信聊天记录。

理解规则：
1. 用户说“聊天记录”“总结聊天”“最近微信聊了啥”“微信重点”且没有明确联系人/群名时，默认指所有已同步会话的全量聊天记录；只有明确说“今天/昨天/最近一周/最近 N 天或小时”时才按时间范围筛选。
2. 用户明确说“我和某人”“某群”“某联系人”时，只分析对应联系人/群。
3. 回答必须只基于传入的本地检索结果；没有数据就直接说明没看到，不要编造。
4. 对全局聊天记录要主动按会话/主题归纳，优先给要点、风险、待办和异常信号。
5. 任何发给第三方联系人/群聊的消息都必须创建待确认动作，不能直接执行。
6. 回答简洁、中文优先、说明依据来自本地已同步记录。
"""


DIRECT_SYSTEM_PROMPT = """你是 WeChatAI 微信入口里的直接 Claude 助手。
OpenClaw / WeixinClawBot 只是消息转发入口；用户是在和本地 Agent 对话。

回答规则：
1. 普通对话直接回答，不要说自己只是 OpenClaw。
2. 如果用户要查微信聊天记录，应简短提示可以直接问“总结最近聊天记录”或指定联系人；实际记录查询由上层路由处理。
3. 如果用户要修改项目、修复问题、运行测试、加功能，应简短提示这会交给开发 Agent；实际开发执行由上层路由处理。
4. 中文优先，回答简洁。
"""


@dataclass
class AgentReply:
    text: str
    pending_action: dict | None = None
    used_claude: bool = False


def _claude_code_base_url(base_url: str) -> str:
    """Claude Code SDK expects the provider root, not the /v1 endpoint."""
    normalized = (base_url or "").strip().rstrip("/")
    if normalized.lower().endswith("/v1"):
        return normalized[:-3].rstrip("/")
    return normalized


def _claude_code_env(settings: Any) -> dict[str, str]:
    env = {
        "ANTHROPIC_API_KEY": settings.ANTHROPIC_API_KEY,
        "ANTHROPIC_AUTH_TOKEN": settings.ANTHROPIC_API_KEY,
    }
    base_url = _claude_code_base_url(settings.ANTHROPIC_BASE_URL)
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
        env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    return env


def _model_error_summary(exc: Exception | None) -> str:
    if exc is None:
        return "模型没有返回有效文本"
    detail = str(exc)
    upper = detail.upper()
    if "INSUFFICIENT_BALANCE" in upper or "INSUFFICIENT ACCOUNT BALANCE" in upper:
        return "第三方 Claude 网关余额不足"
    if "429" in detail or "TOO MANY REQUESTS" in upper:
        return "模型服务当前限流"
    if "401" in detail or "UNAUTHORIZED" in upper or "INVALID_API_KEY" in upper:
        return "模型 API Key 无效或未授权"
    if "403" in detail or "FORBIDDEN" in upper:
        return "模型服务拒绝访问"
    return "模型调用失败"


class ClaudeDirectAgent:
    def __init__(self):
        self.settings = get_settings()

    async def reply(self, message: str) -> AgentReply:
        stripped = message.strip()
        if not stripped:
            return AgentReply("我收到的是空消息。")
        if not self.settings.ANTHROPIC_API_KEY:
            return AgentReply("Claude 还没有配置 API Key。")

        last_error: Exception | None = None
        try:
            text = await self._claude_query(stripped)
            if text:
                return AgentReply(text=text, used_claude=True)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(f"Direct Claude Agent SDK failed, using Messages API fallback: {exc}")

        try:
            text = await self._anthropic_messages_query(stripped)
            if text:
                return AgentReply(text=text, used_claude=True)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(f"Direct Anthropic Messages API failed: {exc}")

        return AgentReply(self._fallback_model_unavailable_reply(stripped, last_error))

    async def _claude_query(self, message: str) -> str:
        sdk_env = _claude_code_env(self.settings)
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError:
            from claude_code_sdk import ClaudeCodeOptions as ClaudeAgentOptions, query  # type: ignore

        chunks: list[str] = []
        options = ClaudeAgentOptions(
            system_prompt=DIRECT_SYSTEM_PROMPT,
            max_turns=1,
            model=self.settings.CLAUDE_MODEL,
            env=sdk_env,
        )
        async for item in query(prompt=message, options=options):
            text = self._message_text(item)
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()

    async def _anthropic_messages_query(self, message: str) -> str:
        if not self.settings.ANTHROPIC_BASE_URL:
            return ""

        body = json.dumps(
            {
                "model": self.settings.CLAUDE_MODEL,
                "max_tokens": 1200,
                "system": DIRECT_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": message}],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            self.settings.ANTHROPIC_BASE_URL.rstrip("/") + "/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

        parts = []
        for block in payload.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(parts).strip()

    def _fallback_model_unavailable_reply(self, message: str, exc: Exception | None) -> str:
        reason = _model_error_summary(exc)
        compact = re.sub(r"\s+", "", message)
        if any(word in compact for word in ("推送", "直接发", "发给我", "每日总结", "日总结")):
            return (
                f"Claude 当前没有回复成功：{reason}。\n"
                "说明：OpenClaw/WeixinClawBot 只是微信转发入口；后端 Agent 已收到消息。"
                "每日总结当前配置为发到“文件传输助手”，你可以发“现在发一次每日总结”触发一次。"
                "补充 Claude 网关余额或换可用 API Key 后，普通 Claude 对话会自动恢复。"
            )
        return (
            f"Claude 当前没有回复成功：{reason}。\n"
            "后端 Agent 和微信转发是通的，但模型网关没有可用输出。"
            "换可用 Claude API Key 或补充当前网关余额后，会自动恢复正常对话。"
        )

    def _message_text(self, item: Any) -> str:
        if isinstance(item, str):
            return item
        content = getattr(item, "content", None)
        if not content:
            return ""
        parts = []
        for block in content if isinstance(content, list) else [content]:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
            elif isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(parts).strip()


class ClaudeWechatAgent:
    def __init__(self, tools: AgentTools):
        self.tools = tools
        self.settings = get_settings()

    async def reply(self, message: str) -> AgentReply:
        stripped = message.strip()
        if not stripped:
            return AgentReply("我收到的是空消息。你可以问我：总结最近聊天记录、搜索关键词、查某个人或某个群的记录。")

        pending = await self._maybe_create_send_confirmation(stripped)
        if pending:
            payload = pending["payload"]
            text = (
                f"已生成待确认发送动作 {pending['id']}。\n"
                f"收件人：{payload['contact_name']}\n"
                f"内容：{payload['content']}\n\n"
                f"确认发送请回复：确认 {pending['id']}"
            )
            return AgentReply(text=text, pending_action=pending)

        local_context = await self._build_relevant_context(stripped)
        if not self.settings.ANTHROPIC_API_KEY:
            return AgentReply(self._fallback_reply(stripped, local_context))

        try:
            text = await self._claude_query(stripped, local_context)
            if text:
                return AgentReply(text=text, used_claude=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Claude Agent SDK failed, using Messages API fallback: {exc}")

        try:
            text = await self._anthropic_messages_query(stripped, local_context)
            if text:
                return AgentReply(text=text, used_claude=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Anthropic Messages API failed, using local fallback: {exc}")

        return AgentReply(self._fallback_reply(stripped, local_context))

    async def _build_relevant_context(self, message: str) -> dict[str, Any]:
        query = self._extract_query(message)
        hours = self._extract_recent_hours(message)

        if self._is_global_records_request(message, query):
            recent = await self.tools.global_recent_messages(hours=hours, limit=220)
            if not recent and 0 < hours < 168:
                hours = 168
                recent = await self.tools.global_recent_messages(hours=hours, limit=220)
            overview = await self.tools.global_message_overview()
            return {
                "scope": "all_conversations_all_history" if hours <= 0 else "all_conversations_recent",
                "query": query,
                "hours": hours,
                "range": "全部已同步聊天记录" if hours <= 0 else f"最近 {hours} 小时",
                "overview": overview,
                "conversations": [],
                "selected_talker": "",
                "recent_messages": recent,
                "search_hits": [],
            }

        conversations = await self.tools.search_conversations(query or message, limit=6)
        talker = self._select_talker(query, conversations)
        search_hits = await self.tools.search_messages(query or message, talker=talker, limit=50)
        recent = await self.tools.recent_messages(talker, limit=100) if talker else []
        return {
            "scope": "selected_conversation" if talker else "search",
            "query": query,
            "hours": hours,
            "conversations": conversations,
            "selected_talker": talker,
            "recent_messages": recent,
            "search_hits": search_hits,
        }

    def _select_talker(self, query: str, conversations: list[dict]) -> str:
        if len(conversations) == 1:
            return conversations[0]["talker"]
        for item in conversations:
            names = {item.get("name"), item.get("nickname"), item.get("remark"), item.get("alias")}
            if query and query in names:
                return item["talker"]
        return ""

    def _is_global_records_request(self, message: str, query: str) -> bool:
        text = re.sub(r"\s+", "", message)
        if any(word in text for word in ("我和", "跟", "和谁", "哪个群", "某个", "联系人")):
            return False
        record_words = (
            "聊天记录",
            "聊天纪录",
            "会话记录",
            "微信记录",
            "最近聊天",
            "最近记录",
            "微信聊了啥",
            "微信聊什么",
            "微信重点",
            "群里聊了啥",
            "大家聊了啥",
        )
        summary_words = ("总结", "汇总", "看看", "分析", "整理", "复盘", "说一下", "重点", "摘要")
        if any(word in text for word in record_words):
            return True
        if any(word in text for word in summary_words) and any(word in text for word in ("聊天", "微信", "记录", "会话")):
            return True
        return query in {"聊天记录", "最近聊天记录", "最近聊天", "微信聊天记录", "微信重点"}

    def _extract_recent_hours(self, message: str) -> int:
        text = message.replace(" ", "")
        if any(word in text for word in ("全部", "所有", "全量", "全库", "完整", "历史", "以前")):
            return 0
        if "最近24小时" in text or "24h" in text.lower():
            return 24
        if "今天" in text:
            return 24
        if "昨天" in text:
            return 48
        if "最近一周" in text or "这周" in text or "7天" in text:
            return 168
        match = re.search(r"最近(\d+)\s*(小时|天)", message)
        if match:
            amount = int(match.group(1))
            return min(720, amount if match.group(2) == "小时" else amount * 24)
        return 0

    def _extract_query(self, message: str) -> str:
        text = message.strip()
        patterns = [
            r"我和(.+?)(?:最近|昨天|今天|上周|这周|聊|说|的|$)",
            r"(?:查|搜索|找)(?:一下)?(.+?)(?:的)?(?:聊天记录|记录|$)",
            r"谁(?:提到|说过)(.+)",
            r"(?:关于|有关)(.+?)(?:的)?(?:聊天记录|记录|消息|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                value = match.group(1).strip(" ：:，,。")
                if value:
                    return value
        if self._looks_like_generic_records_question(text):
            return "最近聊天记录"
        return text[:40]

    def _looks_like_generic_records_question(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        return any(
            word in compact
            for word in (
                "聊天记录",
                "最近聊天",
                "微信记录",
                "会话记录",
                "微信聊了啥",
                "微信重点",
                "群里聊了啥",
                "大家聊了啥",
            )
        )

    async def _maybe_create_send_confirmation(self, message: str) -> dict | None:
        patterns = [
            r"(?:帮我|替我)?发给(.+?)(?:说|：|:)(.+)",
            r"(?:帮我|替我)?给(.+?)(?:发消息|发微信)?(?:说|：|:)(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                contact_name = match.group(1).strip(" ：:，,。")
                content = match.group(2).strip()
                if contact_name and content:
                    return await self.tools.create_send_confirmation(contact_name, content)
        return None

    def _context_prompt(self, message: str, context: dict[str, Any]) -> str:
        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"用户问题：{message}\n\n"
            f"本次检索范围：{context.get('scope')}\n"
            f"可用本地检索结果 JSON：\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            "请基于这些本地结果回答。"
            "如果 scope 是 all_conversations_all_history，请理解为用户要看所有会话的全部已同步聊天记录，"
            "overview 覆盖全量统计，recent_messages 是为控制上下文而抽取的最新代表消息。"
            "如果 scope 是 all_conversations_recent，请理解为用户要看所有会话的指定时间范围聊天记录，"
            "按会话和主题总结，不要说当前入口会话为空。"
            "如果结果不足，请直接说明需要同步或补充更精确的联系人/群名。"
        )

    async def _claude_query(self, message: str, context: dict[str, Any]) -> str:
        sdk_env = _claude_code_env(self.settings)
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError:
            from claude_code_sdk import ClaudeCodeOptions as ClaudeAgentOptions, query  # type: ignore

        chunks: list[str] = []
        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            max_turns=1,
            model=self.settings.CLAUDE_MODEL,
            env=sdk_env,
        )
        async for item in query(prompt=self._context_prompt(message, context), options=options):
            text = self._message_text(item)
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()

    async def _anthropic_messages_query(self, message: str, context: dict[str, Any]) -> str:
        if not self.settings.ANTHROPIC_BASE_URL:
            return ""

        body = json.dumps(
            {
                "model": self.settings.CLAUDE_MODEL,
                "max_tokens": 1600,
                "messages": [{"role": "user", "content": self._context_prompt(message, context)}],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            self.settings.ANTHROPIC_BASE_URL.rstrip("/") + "/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

        parts = []
        for block in payload.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(parts).strip()

    def _message_text(self, item: Any) -> str:
        if isinstance(item, str):
            return item
        content = getattr(item, "content", None)
        if not content:
            return ""
        parts = []
        for block in content if isinstance(content, list) else [content]:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
            elif isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(parts).strip()

    def _fallback_reply(self, message: str, context: dict[str, Any]) -> str:
        conversations = context.get("conversations") or []
        recent = context.get("recent_messages") or []
        hits = context.get("search_hits") or []

        if context.get("scope") in {"all_conversations_recent", "all_conversations_all_history"}:
            if not recent:
                return "我还没看到全局聊天记录。可以先同步微信，或指定更精确的联系人/群名。"
            overview = context.get("overview") or {}
            totals = overview.get("totals") or {}
            range_label = context.get("range") or "全部已同步聊天记录"
            total = totals.get("total_messages") or len(recent)
            convs = totals.get("total_conversations") or "多个"
            first = totals.get("first_date") or ""
            last = totals.get("last_date") or ""
            suffix = f"（{first} 至 {last}）" if first and last else ""
            lines = [f"我先按{range_label}{suffix}整理：共 {convs} 个会话、{total} 条消息。下面是最新代表片段："]
            for item in recent[-12:]:
                speaker = "我" if item.get("is_sender") else (item.get("sender") or item.get("name") or "对方")
                lines.append(f"- {item.get('date', '')} [{item.get('name') or item.get('talker')}] {speaker}：{item.get('content', '')[:120]}")
            return "\n".join(lines)

        if not conversations and not hits:
            return "我还没在已同步聊天记录里找到相关内容。可以先确认微信已经同步，或把联系人/群名说得更精确一点。"

        if len(conversations) > 1 and not context.get("selected_talker"):
            lines = ["我找到多个可能的会话，请你指定一个："]
            for item in conversations[:6]:
                lines.append(f"- {item['name'] or item['talker']}：{item.get('last_message') or ''}")
            return "\n".join(lines)

        rows = recent or hits
        if not rows:
            name = conversations[0]["name"] if conversations else context.get("query", "")
            return f"找到了会话“{name}”，但近期没有可用于回答的文本消息。"

        preview = rows[-8:] if recent else rows[:8]
        lines = [f"我先基于本地记录给你整理一下（问题：{message}）："]
        for item in preview:
            speaker = "我" if item.get("is_sender") else (item.get("sender") or item.get("name") or "对方")
            lines.append(f"- {item.get('date', '')} {speaker}：{item.get('content', '')[:120]}")
        return "\n".join(lines)
