from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.ai.openai_provider import OpenAIProvider
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
1. 普通对话直接回答，不要说自己只是 OpenClaw，也不要把自己降级成“只能检索数据库”的机器人。
2. 结合最近几轮对话理解省略语，例如“那这个呢”“继续”“详细点”“发给我”。
3. 如果用户要查微信聊天记录，不要让用户换问法；说明你会按聊天记录入口处理，除非确实缺少联系人/范围。
4. 如果用户要修改项目、修复问题、运行测试、加功能，应简短说明会交给开发 Agent，不要假装已经执行。
5. 中文优先，回答要像一个能思考的助手：先给结论，再给必要依据。
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

    def _openai_compatible_configured(self) -> bool:
        return (
            self.settings.AI_PROVIDER.lower() == "openai"
            and bool(self.settings.OPENAI_API_KEY)
            and bool(self.settings.OPENAI_BASE_URL)
        )

    async def reply(self, message: str, dialog_history: list[dict] | None = None) -> AgentReply:
        stripped = message.strip()
        if not stripped:
            return AgentReply("我收到的是空消息。")
        if not self.settings.ANTHROPIC_API_KEY and not self._openai_compatible_configured():
            return AgentReply("还没有配置可用的模型 API Key。")

        last_error: Exception | None = None
        if self._openai_compatible_configured():
            try:
                text = await self._openai_messages_query(stripped, dialog_history or [])
                if text:
                    return AgentReply(text=text, used_claude=True)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(f"Direct OpenAI-compatible Agent failed, using Claude fallback: {exc}")

        if not self.settings.ANTHROPIC_API_KEY:
            return AgentReply(self._fallback_model_unavailable_reply(stripped, last_error))

        try:
            text = await self._claude_query(stripped, dialog_history or [])
            if text:
                return AgentReply(text=text, used_claude=True)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(f"Direct Claude Agent SDK failed, using Messages API fallback: {exc}")

        try:
            text = await self._anthropic_messages_query(stripped, dialog_history or [])
            if text:
                return AgentReply(text=text, used_claude=True)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(f"Direct Anthropic Messages API failed: {exc}")

        return AgentReply(self._fallback_model_unavailable_reply(stripped, last_error))

    def _direct_prompt(self, message: str, dialog_history: list[dict]) -> str:
        history = dialog_history[-8:] if dialog_history else []
        return (
            f"最近对话历史 JSON：\n{json.dumps(history, ensure_ascii=False, indent=2)}\n\n"
            f"用户当前消息：{message}\n\n"
            "请结合最近对话历史回答当前消息。若当前消息明显承接上一轮，例如“继续/详细点/这个呢/为什么”，"
            "必须补全指代后回答。"
        )

    async def _openai_messages_query(self, message: str, dialog_history: list[dict]) -> str:
        return (
            await OpenAIProvider().chat(
                [{"role": "user", "content": self._direct_prompt(message, dialog_history)}],
                system_prompt=DIRECT_SYSTEM_PROMPT,
            )
        ).strip()

    async def _claude_query(self, message: str, dialog_history: list[dict]) -> str:
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
        async for item in query(prompt=self._direct_prompt(message, dialog_history), options=options):
            text = self._message_text(item)
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()

    async def _anthropic_messages_query(self, message: str, dialog_history: list[dict]) -> str:
        if not self.settings.ANTHROPIC_BASE_URL:
            return ""

        body = json.dumps(
            {
                "model": self.settings.CLAUDE_MODEL,
                "max_tokens": 4000,
                "system": DIRECT_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": self._direct_prompt(message, dialog_history)}],
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

    def _openai_compatible_configured(self) -> bool:
        return (
            self.settings.AI_PROVIDER.lower() == "openai"
            and bool(self.settings.OPENAI_API_KEY)
            and bool(self.settings.OPENAI_BASE_URL)
        )

    async def reply(self, message: str, dialog_history: list[dict] | None = None) -> AgentReply:
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

        local_context = await self._build_relevant_context(stripped, dialog_history or [])
        if not self.settings.ANTHROPIC_API_KEY and not self._openai_compatible_configured():
            return AgentReply(self._fallback_reply(stripped, local_context))

        if self._openai_compatible_configured():
            try:
                text = await self._openai_messages_query(stripped, local_context)
                if text:
                    return AgentReply(text=text, used_claude=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"OpenAI-compatible Agent failed, using Claude fallback: {exc}")

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

    async def _build_relevant_context(self, message: str, dialog_history: list[dict] | None = None) -> dict[str, Any]:
        expanded_message = self._expand_followup_message(message, dialog_history or [])
        query = self._extract_query(expanded_message)
        hours = self._extract_recent_hours(message)
        knowledge_query = self._knowledge_query(expanded_message, query)
        query_candidates = self._query_candidates(expanded_message, query, knowledge_query)

        if self._is_global_records_request(expanded_message, query):
            recent = await self.tools.global_recent_messages(hours=hours, limit=220)
            if not recent and 0 < hours < 168:
                hours = 168
                recent = await self.tools.global_recent_messages(hours=hours, limit=220)
            overview = await self.tools.global_message_overview()
            knowledge_hits = await self._search_knowledge_candidates(query_candidates, limit=24)
            return {
                "scope": "all_conversations_all_history" if hours <= 0 else "all_conversations_recent",
                "query": query,
                "expanded_message": expanded_message,
                "query_candidates": query_candidates,
                "hours": hours,
                "range": "全部已同步聊天记录" if hours <= 0 else f"最近 {hours} 小时",
                "overview": overview,
                "conversations": [],
                "selected_talker": "",
                "recent_messages": recent,
                "search_hits": [],
                "knowledge_hits": knowledge_hits,
            }

        conversations = await self.tools.search_conversations(query or expanded_message, limit=6)
        talker = self._select_talker(query, conversations)
        search_hits = await self.tools.search_messages(query or expanded_message, talker=talker, limit=50)
        recent = await self.tools.recent_messages(talker, limit=100) if talker else []
        knowledge_hits = await self._search_knowledge_candidates(query_candidates, talker=talker, limit=24)
        return {
            "scope": "selected_conversation" if talker else "search",
            "query": query,
            "expanded_message": expanded_message,
            "query_candidates": query_candidates,
            "hours": hours,
            "conversations": conversations,
            "selected_talker": talker,
            "recent_messages": recent,
            "search_hits": search_hits,
            "knowledge_hits": knowledge_hits,
        }

    def _select_talker(self, query: str, conversations: list[dict]) -> str:
        if len(conversations) == 1:
            return conversations[0]["talker"]
        for item in conversations:
            names = {item.get("name"), item.get("nickname"), item.get("remark"), item.get("alias")}
            if query and query in names:
                return item["talker"]
        return ""

    def _expand_followup_message(self, message: str, dialog_history: list[dict]) -> str:
        compact = re.sub(r"\s+", "", message)
        followup_words = {"继续", "详细点", "展开", "说细点", "这个呢", "那这个呢", "为什么", "还有呢", "然后呢", "再说说"}
        if compact not in followup_words and len(compact) > 12:
            return message
        for item in reversed(dialog_history[-8:]):
            content = str(item.get("content") or "").strip()
            role = str(item.get("role") or "")
            if role == "user" and content and content != message:
                return f"上一轮用户问题：{content}\n当前追问：{message}"
        return message

    def _query_candidates(self, message: str, query: str, knowledge_query: str) -> list[str]:
        candidates: list[str] = []
        for value in (knowledge_query, query, message):
            value = (value or "").strip()
            if value and value not in candidates:
                candidates.append(value)

        cleaned = re.sub(
            r"(最近|今天|昨天|这周|本周|这几天|大家|群里|微信|有没有|是否|关于|有关|对|有什么看法|看法|观点|怎么看|态度|反应|帮我|综合一下|总结一下|总结|聊了什么|聊什么|都聊了啥|都聊什么|聊天记录|记录)",
            " ",
            message,
        )
        cleaned = re.sub(r"[，。！？?、；;：:\s]+", " ", cleaned).strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

        parts = [p.strip() for p in re.split(r"[和与及、,，/]+", cleaned) if len(p.strip()) >= 2]
        for part in parts[:6]:
            if part not in candidates:
                candidates.append(part)
        self._append_domain_query_expansions(message, candidates)
        return candidates[:8]

    def _append_domain_query_expansions(self, message: str, candidates: list[str]) -> None:
        compact = re.sub(r"\s+", "", message.lower())
        expansions: list[str] = []
        if any(term in compact for term in ("美股", "奈飞", "netflix", "nflx", "股票", "股市", "投资")):
            expansions.extend([
                "美股",
                "奈飞",
                "Netflix",
                "NFLX",
                "股票",
                "股市",
                "投资",
                "小声搞钱",
            ])
        if any(term in compact for term in ("币", "btc", "eth", "大饼", "以太")):
            expansions.extend(["BTC", "ETH", "大饼", "以太", "币圈", "合约"])
        if any(term in compact for term in ("汽车", "车", "腾势", "小米", "问界", "m9", "su7")):
            expansions.extend(["汽车", "腾势", "小米汽车", "问界", "M9", "SU7"])
        for item in expansions:
            if item and item not in candidates:
                candidates.append(item)

    async def _search_knowledge_candidates(self, candidates: list[str], talker: str = "", limit: int = 24) -> list[dict]:
        merged: dict[int, dict] = {}
        for candidate in candidates:
            if not candidate.strip():
                continue
            for item in await self.tools.search_knowledge(candidate, talker=talker, limit=limit):
                item_id = int(item.get("id") or 0)
                if not item_id:
                    continue
                if item_id not in merged:
                    item["matched_query"] = candidate
                    merged[item_id] = item
        return sorted(
            merged.values(),
            key=lambda item: (
                float(item.get("score") or 0),
                int(item.get("end_time") or 0),
            ),
            reverse=True,
        )[:limit]

    def _is_global_records_request(self, message: str, query: str) -> bool:
        text = re.sub(r"\s+", "", message)
        if any(word in text for word in ("我和", "跟", "和谁", "哪个群", "某个", "联系人")):
            return False
        if self._is_time_range_only_records_request(text):
            return True
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

    def _is_time_range_only_records_request(self, text: str) -> bool:
        if any(word in text for word in ("今天", "昨天", "这周", "上周", "最近一周")):
            return True
        if re.fullmatch(r"最近\d+(?:个)?(?:小时|天|周|月)(?:的)?", text):
            return True
        return False

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
        match = re.search(r"最近(\d+)\s*(小时|天|周|月)", message)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            multiplier = {"小时": 1, "天": 24, "周": 168, "月": 720}[unit]
            return min(720, amount * multiplier)
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
        if self._is_time_range_only_records_request(re.sub(r"\s+", "", text)):
            return "最近聊天记录"
        return text[:40]

    def _knowledge_query(self, message: str, query: str) -> str:
        text = re.sub(r"\s+", "", message)
        generic = {
            "聊天记录",
            "最近聊天记录",
            "最近聊天",
            "微信聊天记录",
            "微信重点",
        }
        if query in generic or self._is_time_range_only_records_request(text):
            return ""
        return query or message

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
        wants_json = bool(re.search(r"\bjson\b|结构化|schema|字段", message, flags=re.IGNORECASE))
        output_instruction = (
            "输出格式硬性要求：用户原文没有明确要求 JSON/json/结构化/schema 时，必须用自然中文段落和项目符号回答，禁止输出 JSON，禁止输出代码块。"
            if not wants_json
            else "输出格式硬性要求：用户明确要求 JSON/结构化输出时，只输出合法 JSON，不要 Markdown，不要解释性前后缀。"
        )
        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"用户问题：{message}\n\n"
            f"本次检索范围：{context.get('scope')}\n"
            f"可用本地检索结果 JSON（注意：这是输入证据格式，不是输出格式）：\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            f"{output_instruction}"
            "请像一个聪明的微信聊天记录分析 agent 一样回答：先判断用户真实意图，再综合证据，不要机械复述检索片段。"
            "knowledge_hits 是长期知识库/RAG 检索命中的聊天块；只要 knowledge_hits 非空，就代表已经找到相关聊天内容，"
            "必须基于这些知识块回答，不能说没有找到。可以按 title/name 引用来源。"
            "search_hits 为空不等于没找到，因为长期知识库可能已经命中。"
            "query_candidates 是系统根据用户自然语言自动改写出来的检索词，matched_query 表示该片段由哪个查询命中。"
            "回答时优先使用 message_count 多、时间新、与用户问题最贴近的片段；如果命中分散，要合并成主题，而不是逐条罗列。"
            "默认必须详细回答，不要只给短摘要。格式建议：先给一句话结论；然后给 8-15 个细分要点，每个要点带来源群/联系人、日期、原话/证据、你的判断；最后给待办、风险、后续可追问。"
            "如果用户原文明确要求 JSON，也必须输出详细 JSON：字段里要包含 detailed_summary、topic_breakdown、evidence_items、risks、open_questions、next_actions；signals 不能只有一条，除非证据确实只有一条。"
            "只有用户问题原文出现 JSON/json/结构化输出/API schema/字段 时才输出 JSON；否则即使检索证据是 JSON，也必须输出自然中文。"
            "投资/交易类问题要把 market_notes、signals、rationale、evidence、risk 写具体：每个标的至少说明消息来源、讨论背景、支持理由、反对/不确定因素、需要继续观察的触发条件。"
            "如果用户问“最近/大家/有没有聊”，默认是在问所有微信聊天记录，不要只看 WeixinClawBot 入口这一个会话。"
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

    async def _openai_messages_query(self, message: str, context: dict[str, Any]) -> str:
        return (
            await OpenAIProvider().chat(
                [{"role": "user", "content": self._context_prompt(message, context)}],
                system_prompt=SYSTEM_PROMPT,
            )
        ).strip()

    async def _anthropic_messages_query(self, message: str, context: dict[str, Any]) -> str:
        if not self.settings.ANTHROPIC_BASE_URL:
            return ""

        body = json.dumps(
            {
                "model": self.settings.CLAUDE_MODEL,
                "max_tokens": 5000,
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
        knowledge_hits = context.get("knowledge_hits") or []

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
            if knowledge_hits:
                lines = [f"我先从知识库里找到这些相关片段（问题：{message}）："]
                for item in knowledge_hits[:12]:
                    snippet = (item.get("text") or "").replace("\n", " ")[:420]
                    lines.append(f"- {item.get('title') or item.get('name')}：{snippet}")
                return "\n".join(lines)
            return "我还没在已同步聊天记录或知识库里找到相关内容。可以先确认微信已经同步，或把联系人/群名说得更精确一点。"

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
