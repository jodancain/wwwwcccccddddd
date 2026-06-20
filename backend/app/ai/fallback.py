from collections import Counter, defaultdict
from datetime import datetime
from itertools import islice


def provider_error_message(error: Exception) -> str:
    text = str(error)
    if "429" in text or "Too Many Requests" in text:
        return "外部 AI 当前被限流了。我先用本地聊天记录分析给你一个可用结果。"
    if "401" in text or "Unauthorized" in text:
        return "外部 AI 密钥不可用或已失效。我先用本地聊天记录分析给你一个可用结果。"
    return "外部 AI 暂时不可用。我先用本地聊天记录分析给你一个可用结果。"


def _display_name(msg: dict) -> str:
    return msg.get("remark") or msg.get("nickname") or msg.get("talker") or "未知会话"


def _format_time(ts: int | float | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _clean_content(msg: dict) -> str:
    content = (msg.get("content") or "").strip()
    if content:
        return content
    type_name = msg.get("type_name") or "消息"
    return f"[{type_name}]"


def fallback_replies(messages: list[dict]) -> list[str]:
    incoming = [m for m in messages if not m.get("is_sender")]
    last = _clean_content(incoming[-1]) if incoming else ""
    if not last:
        return ["收到，我看一下。", "好的，我稍后回复你。", "明白，我确认后跟你说。"]

    question_marks = ("?", "？", "吗", "么", "哪", "谁", "什么时候", "怎么", "多少")
    if any(mark in last for mark in question_marks):
        return [
            "我看到了，我确认一下再回复你。",
            "可以，我先核对一下细节，稍后跟你说。",
            "这个我需要看下具体情况，确认后给你答复。",
        ]

    return [
        "收到。",
        "好的，我知道了。",
        "明白，谢谢你同步。",
    ]


def fallback_chat_response(user_message: str, messages: list[dict], error: Exception | None = None) -> str:
    prefix = provider_error_message(error) if error else "我先基于本地聊天记录帮你看一下。"
    if not messages:
        return f"{prefix}\n\n当前没有选中的聊天记录，所以只能给通用建议。"

    name = _display_name(messages[-1])
    recent = [m for m in messages if _clean_content(m)][-8:]
    lines = []
    for msg in recent:
        who = "我" if msg.get("is_sender") else (msg.get("sender") or name)
        lines.append(f"- {_format_time(msg.get('create_time'))} {who}: {_clean_content(msg)[:120]}")

    replies = fallback_replies(messages)
    return (
        f"{prefix}\n\n"
        f"当前会话：{name}\n"
        f"最近消息：\n" + "\n".join(lines) + "\n\n"
        f"可参考回复：\n"
        f"1. {replies[0]}\n2. {replies[1]}\n3. {replies[2]}"
    )


def fallback_global_summary(messages: list[dict], hours: int, error: Exception | None = None) -> str:
    prefix = provider_error_message(error) if error else "我先基于本地聊天记录生成摘要。"
    if not messages:
        return f"{prefix}\n\n最近 {hours} 小时没有聊天记录。"

    grouped: dict[str, list[dict]] = defaultdict(list)
    for msg in messages:
        grouped[msg.get("talker") or "unknown"].append(msg)

    active = sorted(grouped.values(), key=lambda items: items[-1].get("create_time", 0), reverse=True)
    total = len(messages)
    group_count = len(grouped)
    senders = Counter("我" if m.get("is_sender") else _display_name(m) for m in messages)

    top_sections = []
    for items in islice(active, 12):
        last = items[-1]
        name = _display_name(last)
        last_text = _clean_content(last)[:160]
        top_sections.append(
            f"- {name}：{len(items)} 条，最后 {_format_time(last.get('create_time'))}，最新内容：{last_text}"
        )

    todo_keywords = ("记得", "麻烦", "帮我", "需要", "确认", "安排", "报名", "付款", "发我", "回复")
    todos = []
    for msg in messages:
        text = _clean_content(msg)
        if any(k in text for k in todo_keywords):
            todos.append(f"- {_display_name(msg)} {_format_time(msg.get('create_time'))}：{text[:120]}")
        if len(todos) >= 10:
            break

    speaker_lines = [f"- {name}: {count} 条" for name, count in senders.most_common(8)]
    todo_text = "\n".join(todos) if todos else "- 暂未从关键词中识别到明确待办。"

    return (
        f"{prefix}\n\n"
        f"## 总览\n最近 {hours} 小时共有 {group_count} 个会话、{total} 条消息。\n\n"
        f"## 活跃会话\n" + "\n".join(top_sections) + "\n\n"
        f"## 发言概览\n" + "\n".join(speaker_lines) + "\n\n"
        f"## 可能需要跟进\n{todo_text}"
    )
