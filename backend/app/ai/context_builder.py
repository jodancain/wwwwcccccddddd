from datetime import datetime


SYSTEM_PROMPT = """你是一个智能微信助手，嵌入在用户的微信客户端中。你能看到用户正在查看的微信对话的完整历史记录。

你的职责：
1. 帮助用户理解对话内容、分析对方意图和语气
2. 根据对话上下文，给出合适的回复建议
3. 回答用户关于对话内容的任何问题
4. 提供专业、得体、有同理心的沟通建议

注意事项：
- 用中文回复
- 你可以看到对话的完整历史记录，包括较早的消息
- 回复建议要自然、得体，符合微信聊天风格
- 注意区分群聊和私聊场景
- 注意保护隐私，不要泄露敏感信息
- 如果用户让你"帮我回复"，给出2-3个不同风格的回复选项"""

SUGGEST_REPLY_PROMPT = """根据以下微信对话内容，生成3条合适的回复建议。

要求：
1. 每条回复都要自然、得体，适合微信聊天
2. 3条回复分别代表不同的风格：
   - 第1条：简洁直接
   - 第2条：热情友好
   - 第3条：专业正式
3. 只输出3条回复，用JSON数组格式：["回复1", "回复2", "回复3"]
4. 不要加额外解释"""


def build_conversation_context(messages: list[dict], talker_name: str = "",
                                is_group: bool = False, max_chars: int = 30000) -> str:
    """Build conversation context from all messages.

    For large conversations, older messages are condensed (date + count summary)
    while recent messages are kept in full detail. Total output is capped at max_chars.
    """
    if not messages:
        return "当前没有对话消息。"

    chat_type = "群聊" if is_group else "私聊"
    total = len(messages)

    # If messages fit within budget, show all
    all_lines = _format_messages(messages, talker_name, is_group)
    full_text = "\n".join(all_lines)

    if len(full_text) <= max_chars:
        header = f"--- 以下是与「{talker_name or '未知联系人'}」的{chat_type}完整记录（共{total}条消息）---\n"
        return header + full_text + "\n\n--- 对话记录结束 ---"

    # Too large: keep recent messages in detail, summarize older ones by date
    # Binary search for how many recent messages fit
    recent_count = _find_recent_fit(messages, talker_name, is_group, max_chars - 2000)
    older_messages = messages[:len(messages) - recent_count]
    recent_messages = messages[len(messages) - recent_count:]

    parts = []
    parts.append(f"--- 与「{talker_name or '未知联系人'}」的{chat_type}记录（共{total}条消息）---\n")

    # Summarize older messages by date
    if older_messages:
        parts.append(f"【较早的消息概要（共{len(older_messages)}条）】")
        date_groups: dict[str, list] = {}
        for msg in older_messages:
            date_key = msg.get("create_date", "未知日期")
            if date_key not in date_groups:
                date_groups[date_key] = []
            date_groups[date_key].append(msg)

        for date_key in sorted(date_groups.keys()):
            day_msgs = date_groups[date_key]
            # Show date with count and a few sample messages
            samples = []
            for m in day_msgs[:3]:
                content = m.get("content", "")[:40]
                if content:
                    direction = "我" if m.get("is_sender") else (m.get("sender") or talker_name or "对方")
                    samples.append(f"{direction}: {content}")
            sample_text = " | ".join(samples)
            more = f"...等{len(day_msgs)}条" if len(day_msgs) > 3 else f"共{len(day_msgs)}条"
            parts.append(f"  {date_key}: {more} — {sample_text}")

        parts.append("")

    # Show recent messages in full detail
    parts.append(f"【最近的{len(recent_messages)}条消息（完整内容）】\n")
    recent_lines = _format_messages(recent_messages, talker_name, is_group)
    parts.extend(recent_lines)
    parts.append("\n--- 对话记录结束 ---")

    return "\n".join(parts)


def _format_messages(messages: list[dict], talker_name: str, is_group: bool) -> list[str]:
    """Format messages into readable lines."""
    lines = []
    for msg in messages:
        ts = msg.get("create_time", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
        content = msg.get("content", "")
        is_sender = msg.get("is_sender", 0)

        if is_sender:
            direction = "[我]"
        elif is_group and msg.get("sender"):
            direction = f"[{msg.get('sender', '')}]"
        else:
            direction = f"[{talker_name or '对方'}]"

        lines.append(f"{time_str} {direction} {content}")
    return lines


def _find_recent_fit(messages: list[dict], talker_name: str, is_group: bool,
                     budget: int) -> int:
    """Find how many recent messages fit within the character budget."""
    lo, hi = 50, len(messages)
    best = 50  # minimum
    while lo <= hi:
        mid = (lo + hi) // 2
        recent = messages[len(messages) - mid:]
        text = "\n".join(_format_messages(recent, talker_name, is_group))
        if len(text) <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best
