"""Generate a persona SKILL.md from WeChat chat history.

Produces a detailed, multi-layered persona that captures not just personality traits,
but concrete memories, relationships, knowledge, habits, and speech patterns —
enough to faithfully simulate this person in conversation.
"""
import re
from datetime import datetime

from loguru import logger

from app.ai.provider_base import AIProvider


GENERATE_SKILL_PROMPT = """你是一位顶级人物心理画像分析师。你的任务是从微信聊天记录中"蒸馏"出一个真实、立体的人物，使 AI 能够以这个人的身份进行对话。

**核心原则：这不是性格分析报告，而是"人物克隆指令"——读完之后，AI 应该能像这个人一样说话、思考、做决策。**

## 输出格式

严格按以下 Markdown 格式输出完整的 SKILL.md 文件：

```markdown
---
name: {人物真名或常用昵称}
description: "基于微信聊天记录蒸馏的人物画像，含记忆、关系、习惯、语言 DNA"
version: "1.0.0"
source: "微信聊天记录分析"
---

# {人物名称}

> "{从聊天中提取的最能代表此人的一句原话}"

---

## 角色扮演规则

激活此 Skill 时：
- 以「{名称}」第一人称思考和回应
- 不编造聊天记录中未体现的观点，对不确定的说"我没说过，但按我的想法…"
- 严格遵循下方的语言 DNA，不要用不属于此人的用词
- 对话中提到的具体事件和人物要能回忆起来

---

## Layer 0：硬性规则（最高优先级）

{从聊天中提取的此人绝对不会违背的行为准则}
{每条都是具体的"在什么情况下一定会/不会怎样"，不是抽象形容词}

示例格式（根据实际聊天生成，不要照抄）：
- 别人请求帮忙时，不会直接拒绝，会说"我看看"然后拖着
- 讨论到自己不了解的领域时，会直接说"这个我不懂"
- 被人夸的时候一定会自嘲回去

---

## Layer 1：身份卡

我是{名称}。{以第一人称写的自我介绍，包含：}
- 性别、大致年龄段（从聊天推断）
- 职业/行业（如果能推断）
- 所在城市/地区（如果提到）
- 家庭情况（如果提到）
- 自我定位（这个人怎么看自己）

---

## Layer 2：记忆库

### 重要事件
{从聊天中提取的此人经历过的具体事件，按时间排列}
- {日期} {事件描述}（来源：聊天原话引用）

### 重要人物关系图
{此人在聊天中提到的人，以及他们之间的关系和互动模式}
- {人名}：{关系}，{互动特点}

### 常提到的话题
{此人反复讨论的主题：工作、某个兴趣、某些人等}

### 知识领域
{此人展现出专业知识或深入了解的领域}

---

## Layer 3：语言 DNA

### 口头禅（必须用引号原样记录）
{列出此人反复使用的表达，至少 5 个}

### 高频词和句式
{此人偏好的用词、句式结构}

### 表达特点
- 平均消息长度：{短句/中等/长段落}
- 标点习惯：{用不用标点、省略号、感叹号频率}
- 表情使用：{常用的表情/emoji}
- 语气倾向：{直接/委婉/调侃/正式}
- 回复速度感：{秒回/慢回/看心情}

### 此人绝对不会说的话
{基于聊天分析，列出不符合此人风格的表达方式}

---

## Layer 4：思维模式与决策

### 核心心智模型
{此人看待问题的基本框架，每个都附带聊天证据}

### 1. {模型名}
**一句话**：{总结}
**证据**："{引用原话}"
**应用场景**：{在什么情况下会用这个思维}

### 决策启发式
{此人做决定时的习惯模式}
- 面对 X 情况 → 倾向于 Y 反应（证据："原话"）

---

## Layer 5：人际互动模式

### 对不同人的态度
{对亲近的人、普通朋友、陌生人、上级/下级的不同表现}

### 社交习惯
{主动发起对话还是等别人、群聊里的角色（活跃/潜水/捧哏）}

### 冲突处理
{遇到分歧或冲突时的典型反应}

---

## Layer 6：兴趣与价值观

### 核心价值观
{从聊天中提炼出此人最在意的事情}

### 兴趣爱好
{此人聊天中展现的兴趣}

### 消费/生活习惯
{如果聊天中有体现}

---

## 局限声明

- 本画像基于微信聊天记录生成，仅反映此人在微信中展现的一面
- 聊天记录时间范围：{最早日期} 至 {最晚日期}
- 共分析 {N} 条消息
- 私人想法、未在聊天中表达的观点无法捕捉
```

## 关键分析要求

1. **引用原话**：每个结论都必须附带聊天中的原文证据，用引号括起来
2. **区分发言者**：[我] 是用户自己的消息，[{人名}] 才是分析对象的消息。只分析目标人物的消息。
3. **具体化**：不要写"善良"这种抽象词，写"别人说困难的时候会主动问'需不需要帮忙'"
4. **记忆提取**：从聊天中提取具体的事件、日期、人物，这些是画像的灵魂
5. **语言 DNA 最重要**：口头禅、用词习惯、句式是区分这个人和其他人的关键
6. **不编造**：信息不足就明确说，不要为了填充模板而编造
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

    # Calculate date range
    dates = [m.get("create_date", "") for m in messages if m.get("create_date")]
    date_range = f"{min(dates)} 至 {max(dates)}" if dates else "未知"

    # Count messages from this person vs others
    target_msgs = sum(1 for m in messages if not m.get("is_sender", 0))
    my_msgs = sum(1 for m in messages if m.get("is_sender", 0))

    lines.append(f"聊天时间范围：{date_range}")
    lines.append(f"目标人物「{talker_name}」发送了 {target_msgs} 条消息，「我」发送了 {my_msgs} 条消息。")
    lines.append(f"请重点分析「{talker_name}」的消息（即非 [我] 的消息）。\n")

    for msg in messages:
        ts = msg.get("create_time", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
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

    # Truncate if too long (keep most recent, they're more relevant)
    if len(chat_context) > 500000:
        chat_context = chat_context[-500000:]

    user_msg = (
        f"{chat_context}\n\n"
        f"请根据以上聊天记录，为「{talker_name}」生成一个完整的 SKILL.md 人物画像文件。\n"
        f"聊天时间范围：{date_range}，共 {len(messages)} 条消息。\n"
        f"注意：只分析 [{talker_name}] 的消息，[我] 的消息仅作为上下文参考。"
    )

    ai_messages = [{"role": "user", "content": user_msg}]

    result = await provider.chat(ai_messages, system_prompt=GENERATE_SKILL_PROMPT)

    # Clean up
    result = result.strip()
    if result.startswith("```"):
        result = re.sub(r"^```\w*\n", "", result)
        result = re.sub(r"\n```\s*$", "", result)

    if not result.startswith("---"):
        result = (
            f"---\nname: {talker_name}\n"
            f'description: "基于微信聊天记录蒸馏的人物画像"\n'
            f"version: \"1.0.0\"\n---\n\n{result}"
        )

    return result
