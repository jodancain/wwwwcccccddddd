"""Generate a persona SKILL.md from WeChat chat history."""
import json
import re
from datetime import datetime

from loguru import logger

from app.ai.provider_base import AIProvider
from app.ai.context_builder import _format_messages


GENERATE_SKILL_PROMPT = """你是一个人物分析专家。根据用户提供的微信聊天记录，分析这个人的性格、说话风格、行为模式，生成一个 SKILL.md 文件。

## 输出格式

严格按照以下 Markdown 格式输出完整的 SKILL.md 文件内容：

```
---
name: {人物名称或昵称}
description: "基于微信聊天记录分析生成的人物画像"
version: "1.0.0"
---

# {人物名称} 视角

> "{从聊天中提取的一句代表性话语}"

---

## 角色扮演规则

- 以{人物名称}第一人称思考和回应
- 保持口语化、真实的聊天风格
- 不编造未在聊天记录中体现的立场

---

## 身份卡

{根据聊天内容推断的身份信息：性别、职业、性格特点等}

---

## 核心性格特征

{列出3-5个核心性格特征，每个都附带聊天记录中的证据}

### 1. {特征名}
**表现**：{具体描述}
**证据**：{引用聊天中的原话}

---

## 说话风格

### 口头禅和高频词
{列出这个人常用的表达}

### 表达特点
{描述说话习惯：语气、用词、句式等}

### 聊天习惯
{发消息的频率、时间段、回复速度等}

---

## 决策模式

{从聊天中分析出的决策习惯和思维方式}

---

## 人际关系风格

{对不同人的态度、社交习惯等}

---

## 价值观和关注点

{从聊天内容中提取的核心价值观、感兴趣的话题}

---

## 局限声明

- 此画像基于有限的微信聊天记录生成
- 仅反映此人在微信聊天中展现的一面
- 不代表完整人格，请谨慎使用
```

## 分析要求

1. 只从聊天记录中提取事实，不编造
2. 多引用原话作为证据
3. 注意区分这个人发的消息和别人发的消息
4. 如果信息不足，明确说明
5. 中文输出
"""


async def generate_skill_from_chat(
    provider: AIProvider,
    messages: list[dict],
    talker_name: str,
    is_group: bool = False,
) -> str:
    """Generate a SKILL.md from chat messages using AI."""

    # Build chat context
    lines = []
    lines.append(f"以下是「{talker_name}」的微信聊天记录（共{len(messages)}条消息）：\n")

    for msg in messages:
        ts = msg.get("create_time", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
        content = msg.get("content", "")
        if not content or content.startswith("["):
            continue
        is_sender = msg.get("is_sender", 0)

        if is_sender:
            direction = "[我]"
        elif is_group and msg.get("sender"):
            direction = f"[{msg['sender']}]"
        else:
            direction = f"[{talker_name}]"

        lines.append(f"{time_str} {direction} {content}")

    chat_context = "\n".join(lines)

    # Truncate if too long (keep most recent)
    if len(chat_context) > 400000:
        chat_context = chat_context[-400000:]

    user_msg = f"{chat_context}\n\n请根据以上聊天记录，为「{talker_name}」生成一个完整的 SKILL.md 人物画像文件。"

    ai_messages = [{"role": "user", "content": user_msg}]

    result = await provider.chat(ai_messages, system_prompt=GENERATE_SKILL_PROMPT)

    # Clean up: extract the markdown content
    result = result.strip()
    # Remove code fences if wrapped
    if result.startswith("```"):
        result = re.sub(r"^```\w*\n", "", result)
        result = re.sub(r"\n```\s*$", "", result)

    # Ensure it starts with frontmatter
    if not result.startswith("---"):
        result = f"---\nname: {talker_name}\ndescription: \"基于微信聊天记录生成的人物画像\"\nversion: \"1.0.0\"\n---\n\n{result}"

    return result
