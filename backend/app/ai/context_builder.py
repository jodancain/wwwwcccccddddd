from datetime import datetime


SYSTEM_PROMPT = """你是一个智能微信助手，嵌入在用户的微信客户端中。你能看到用户当前选择的微信对话历史记录。

你的职责：
1. 帮助用户理解对话内容，分析对方意图和语气。
2. 根据对话上下文给出合适的回复建议。
3. 回答用户关于对话内容的问题。
4. 提供专业、得体、有同理心的沟通建议。

注意事项：
- 用中文回答。
- 回复建议要自然、得体，符合微信聊天风格。
- 注意区分群聊和私聊场景。
- 保护隐私，不主动泄露敏感信息。
- 如果用户让你“帮我回复”，给出 2 到 3 个不同风格的回复选项。"""


SUGGEST_REPLY_PROMPT = """根据以上微信对话内容，生成 3 条合适的回复建议。

要求：
1. 每条回复都要自然、得体，适合微信聊天。
2. 3 条回复分别代表不同风格：简洁直接、热情友好、专业正式。
3. 只输出 3 条回复，使用 JSON 数组格式，例如：["回复1", "回复2", "回复3"]。
4. 不要添加额外解释。"""


GLOBAL_SUMMARY_SYSTEM_PROMPT = """你是用户的私人微信情报分析助手，可以读取用户所有微信对话的消息记录。
用户要的是“详细日报”，不是泛泛摘要。请只基于提供的聊天记录输出，不要编造。

输出要求：
1. 中文输出，信息密度高，默认写详细版，除非用户明确说“简短”。
2. 不要只写“讨论了投资/汽车/生活”这种空话；要写清楚谁/哪个群、在聊什么、出现了哪些具体事实、观点、分歧、风险、待回复点。
3. 对重要群聊或私聊按“群/联系人”展开，每个重要对话至少 2-4 条具体要点。
4. 对投资、项目、合作、求职、钱、时间安排、需要回复的人优先级更高。
5. 如果某条内容需要用户行动，要说明“为什么需要跟进”和“建议怎么回/怎么做”。
6. 可以引用少量关键原话或关键词，但不要大段复制聊天记录。
7. 如果信息不足，要说明不足在哪里，不要硬凑。

请按以下结构输出：

## 一句话总览
用 1-2 句说明过去 24 小时最重要的变化和风险。

## 数据概览
写明对话数、消息数、最活跃的几个会话，以及主要主题。

## 重点对话详解
按重要程度列出 8-15 个对话。每个对话使用：
### 群/联系人名
- 发生了什么：具体说明。
- 关键观点/信息：列出具体事实、数字、链接、人物、项目或讨论分歧。
- 需要你做什么：无需/需关注/需回复/需决策，并给一句建议。

## 主题归纳
按主题归纳，例如：求职、人际、汽车、投资、项目、生活。每个主题写具体内容和来源会话。

## 待办和需要回复
列出明确待办、潜在待办、建议回复对象。没有也要说“暂无明确必须回复”。

## 风险和机会
列出投资风险、项目风险、关系风险、信息机会。

## 明天建议关注
给 3-8 条明天应关注的事项。"""


def build_global_context(messages: list[dict], max_chars: int = 180000) -> str:
    """Build context from messages across all conversations."""
    if not messages:
        return "没有聊天记录。"

    groups: dict[str, list[dict]] = {}
    talker_names: dict[str, str] = {}
    for msg in messages:
        talker = msg.get("talker", "")
        if talker not in groups:
            groups[talker] = []
            talker_names[talker] = msg.get("remark") or msg.get("nickname") or talker
        groups[talker].append(msg)

    sorted_talkers = sorted(groups.keys(), key=lambda t: groups[t][-1].get("create_time", 0), reverse=True)

    total_msgs = sum(len(g) for g in groups.values())
    dates = [msg.get("create_date", "") for msg in messages if msg.get("create_date")]
    date_range = f"{min(dates)} 至 {max(dates)}" if dates else "未知日期"
    parts = [f"以下是微信聊天记录，范围 {date_range}，共 {len(groups)} 个对话、{total_msgs} 条消息。\n"]
    char_count = len(parts[0])

    for talker in sorted_talkers:
        msgs = groups[talker]
        name = talker_names[talker]
        is_group = bool(msgs[0].get("is_group", 0))
        chat_type = "群聊" if is_group else "私聊"
        lines = [f"\n===== {name} ({chat_type}, {len(msgs)} 条消息) ====="]

        # Keep enough recent detail for a useful daily report while staying under
        # the global prompt budget.
        for msg in msgs[-160:]:
            content = (msg.get("content") or "").strip()
            if not content:
                content = f"[{msg.get('type_name') or '消息'}]"
            details = _source_details(msg, limit=700)
            if details:
                content = f"{content}\n{details}"
            ts = msg.get("create_time", 0)
            time_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
            if msg.get("is_sender"):
                direction = "[我]"
            elif is_group and msg.get("sender"):
                direction = f"[{msg['sender']}]"
            else:
                direction = f"[{name}]"
            lines.append(f"{time_str} {direction} {content}")

        section = "\n".join(lines)
        if char_count + len(section) > max_chars:
            parts.append(f"\n===== {name} ({chat_type}, {len(msgs)} 条消息) =====")
            parts.append(f"内容较多，已省略。最后一条：{(msgs[-1].get('content') or '')[:80]}")
            continue

        parts.append(section)
        char_count += len(section)

    return "\n".join(parts)


def build_conversation_context(
    messages: list[dict],
    talker_name: str = "",
    is_group: bool = False,
    max_chars: int = 120000,
) -> str:
    """Build a readable context block for a single conversation."""
    if not messages:
        return "当前没有对话消息。"

    chat_type = "群聊" if is_group else "私聊"
    total = len(messages)
    all_lines = _format_messages(messages, talker_name, is_group)
    full_text = "\n".join(all_lines)

    if len(full_text) <= max_chars:
        header = f"--- 以下是与“{talker_name or '未知联系人'}”的{chat_type}记录，共 {total} 条消息 ---\n"
        return header + full_text + "\n\n--- 对话记录结束 ---"

    recent_count = _find_recent_fit(messages, talker_name, is_group, max_chars - 3000)
    older_messages = messages[: len(messages) - recent_count]
    recent_messages = messages[len(messages) - recent_count :]

    parts = [f"--- 与“{talker_name or '未知联系人'}”的{chat_type}记录，共 {total} 条消息 ---\n"]
    if older_messages:
        parts.append(f"【较早消息概要，共 {len(older_messages)} 条】")
        date_groups: dict[str, list[dict]] = {}
        for msg in older_messages:
            date_groups.setdefault(msg.get("create_date", "未知日期"), []).append(msg)

        for date_key in sorted(date_groups.keys()):
            day_msgs = date_groups[date_key]
            samples = []
            for msg in day_msgs[:3]:
                content = (msg.get("content") or "").strip()[:40]
                if content:
                    who = "我" if msg.get("is_sender") else (msg.get("sender") or talker_name or "对方")
                    samples.append(f"{who}: {content}")
            sample_text = " | ".join(samples)
            parts.append(f"  {date_key}: 共 {len(day_msgs)} 条。{sample_text}")
        parts.append("")

    parts.append(f"【最近 {len(recent_messages)} 条消息】")
    parts.extend(_format_messages(recent_messages, talker_name, is_group))
    parts.append("\n--- 对话记录结束 ---")
    return "\n".join(parts)


def _format_messages(messages: list[dict], talker_name: str, is_group: bool) -> list[str]:
    lines = []
    for msg in messages:
        ts = msg.get("create_time", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
        content = (msg.get("content") or "").strip()
        if not content:
            content = f"[{msg.get('type_name') or '消息'}]"
        details = _source_details(msg, limit=700)
        if details:
            content = f"{content}\n{details}"

        if msg.get("is_sender"):
            direction = "[我]"
        elif is_group and msg.get("sender"):
            direction = f"[{msg.get('sender', '')}]"
        else:
            direction = f"[{talker_name or '对方'}]"

        lines.append(f"{time_str} {direction} {content}")
    return lines


def _source_details(msg: dict, limit: int = 700) -> str:
    parts = []
    for enriched in msg.get("source_enrichments") or []:
        if enriched.get("status") != "ok":
            continue
        text = " ".join((enriched.get("extracted_text") or "").split())
        if not text:
            continue
        label = "链接解析" if enriched.get("kind") == "link" else "图片解析"
        parts.append(f"{label}: {text[:limit]}")
    return "\n".join(parts)


def _find_recent_fit(messages: list[dict], talker_name: str, is_group: bool, budget: int) -> int:
    lo, hi = 1, len(messages)
    best = min(50, len(messages))
    while lo <= hi:
        mid = (lo + hi) // 2
        recent = messages[len(messages) - mid :]
        text = "\n".join(_format_messages(recent, talker_name, is_group))
        if len(text) <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best
