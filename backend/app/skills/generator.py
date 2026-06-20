"""Generate a persona SKILL.md from WeChat chat history."""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from app.ai.provider_base import AIProvider


GENERATE_SKILL_PROMPT = """你是一位人物画像分析师。请从微信聊天记录中提炼一个可用于 AI 扮演的人物 Skill。

要求：
1. 只分析目标人物的消息，“我”的消息只作为上下文。
2. 不要编造聊天记录里没有体现的信息。
3. 尽量引用原话，提炼口头禅、常见话题、表达风格、互动习惯。
4. 输出完整的 SKILL.md，必须包含 YAML front matter。
5. 用中文输出。

建议结构：
---
name: 人物名称
description: "基于微信聊天记录生成的人物画像"
version: "1.0.0"
source: "微信聊天记录分析"
---

# 人物名称

## 角色扮演规则
## 身份与背景
## 重要记忆
## 语言风格
## 思维与决策模式
## 人际互动模式
## 不确定与局限
"""


def _message_lines(messages: list[dict], talker_name: str, is_group: bool, max_chars: int = 120000) -> tuple[str, str]:
    dates = [m.get("create_date", "") for m in messages if m.get("create_date")]
    date_range = f"{min(dates)} 至 {max(dates)}" if dates else "未知"
    lines = [
        f"以下是“{talker_name}”的微信聊天记录，共 {len(messages)} 条消息。",
        f"聊天时间范围：{date_range}",
        "请重点分析非 [我] 的消息。",
        "",
    ]

    for msg in messages:
        content = (msg.get("content") or "").strip()
        if not content or content.startswith("["):
            continue
        ts = msg.get("create_time", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
        if msg.get("is_sender"):
            speaker = "我"
        elif is_group and msg.get("sender"):
            speaker = msg["sender"]
        else:
            speaker = talker_name
        lines.append(f"{time_str} [{speaker}] {content}")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text, date_range


def _target_texts(messages: list[dict]) -> list[str]:
    return [
        (m.get("content") or "").strip()
        for m in messages
        if not m.get("is_sender") and (m.get("content") or "").strip() and not (m.get("content") or "").startswith("[")
    ]


def fallback_skill_from_chat(messages: list[dict], talker_name: str, is_group: bool = False) -> str:
    texts = _target_texts(messages)
    _, date_range = _message_lines(messages, talker_name, is_group, max_chars=2000)
    sample = texts[-12:]
    words = Counter()
    stopwords = {
        "http", "https", "www", "com", "the", "and", "for", "with", "this", "that", "you", "your",
        "are", "was", "from", "have", "not", "but", "all", "can", "app", "apps", "一个", "这个",
        "那个", "就是", "可以", "还是", "没有", "什么", "怎么", "不是",
    }
    for text in texts:
        for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,}", text):
            normalized = token.lower()
            if normalized in stopwords or normalized.isdigit():
                continue
            if len(normalized) <= 2 and not re.search(r"[\u4e00-\u9fff]", normalized):
                continue
            words[token] += 1
    frequent = [w for w, _ in words.most_common(20)]
    avg_len = round(sum(len(t) for t in texts) / max(1, len(texts)), 1)

    quote_lines = "\n".join(f"- “{t[:120]}”" for t in sample[:8]) or "- 暂无足够原话样本。"
    topic_lines = "\n".join(f"- {w}" for w in frequent[:10]) or "- 暂无足够高频词。"

    return f"""---
name: {talker_name}
description: "基于微信聊天记录生成的人物画像"
version: "1.0.0"
source: "微信聊天记录本地分析"
---

# {talker_name}

## 角色扮演规则
- 以“{talker_name}”的第一人称思考和回复。
- 只使用聊天记录中能支持的表达习惯，不确定的信息要明确说明。
- 回复应贴近微信聊天风格，避免过度正式和长篇说教。

## 基本画像
- 分析范围：{date_range}
- 可分析消息数：{len(texts)} 条
- 平均消息长度：约 {avg_len} 字
- 场景类型：{"群聊" if is_group else "私聊"}

## 常见话题与关键词
{topic_lines}

## 语言风格
- 常用短句和即时反馈，表达偏口语化。
- 会根据上下文快速回应，部分回复较短。
- 下面是最近可参考的原话样本：
{quote_lines}

## 互动建议
- 模拟此人物时，先保持简洁直接，再根据对方追问补充细节。
- 对聊天记录没有体现的事实，不要主动编造。
- 如果需要做决定，优先复用此人最近聊天中出现过的关注点和语气。

## 局限
这是外部 AI 不可用时生成的本地基础画像，细腻程度低于完整模型分析。"""


async def generate_skill_from_chat(
    provider: AIProvider,
    messages: list[dict],
    talker_name: str,
    is_group: bool = False,
) -> str:
    chat_context, date_range = _message_lines(messages, talker_name, is_group)
    target_count = len(_target_texts(messages))
    user_msg = (
        f"{chat_context}\n\n"
        f"请根据以上聊天记录，为“{talker_name}”生成完整的 SKILL.md 人物画像。\n"
        f"聊天时间范围：{date_range}，目标人物消息数：{target_count}。"
    )
    result = await provider.chat([{"role": "user", "content": user_msg}], system_prompt=GENERATE_SKILL_PROMPT)
    result = result.strip()
    if result.startswith("```"):
        result = re.sub(r"^```\w*\n", "", result)
        result = re.sub(r"\n```\s*$", "", result)
    if not result.startswith("---"):
        result = (
            f"---\nname: {talker_name}\n"
            f"description: \"基于微信聊天记录生成的人物画像\"\n"
            f"version: \"1.0.0\"\nsource: \"微信聊天记录分析\"\n---\n\n{result}"
        )
    return result
