from datetime import datetime
import re


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
用户要的是“Plaud 风格的详细日报”：像专业会议纪要一样，把原始对话整理成结构化笔记、决策、待办、风险、机会和可检索线索。

你可能会收到三类材料：
1. 日报证据包：这是从微信记录里预整理出的会话卡片、证据片段、主题标签、链接/图片解析。
2. 微信聊天记录：这是主事实来源。
3. 外部背景快照：这是根据聊天主题自动抓取的公开网页/新闻摘要，只能用于校准现实背景。

必须遵守：
- 不要编造。微信里没说的，不能写成“群里说了/某人说了”。
- 使用外部背景时必须明确写成“外部背景显示/公开信息显示/需要核实”，并尽量带上来源标题或网站名。
- 外部背景只能帮助判断聊天内容的现实意义、风险和后续关注点，不能替代聊天记录。

输出要求：
1. 中文输出，信息密度高，默认写详细版，除非用户明确说“简短”。
2. 不要只写“讨论了投资/汽车/生活”这种空话；要写清楚谁/哪个群、在聊什么、出现了哪些具体事实、观点、分歧、风险、待回复点。
3. 优先使用日报证据包里的会话卡片和证据片段，再用原始聊天记录补充细节。
4. 对重要群聊或私聊按“群/联系人”展开，每个重要对话至少 3-6 条具体要点；消息很多时要覆盖 15-25 个重点对话。
5. 对投资、项目、合作、求职、钱、时间安排、法律、需要回复的人优先级更高。
6. 如果某条内容需要用户行动，要说明“为什么需要跟进”“建议怎么回/怎么做”“建议优先级”。
7. 可以引用少量关键原话或关键词，但不要大段复制聊天记录。
8. 如果信息不足，要说明不足在哪里，不要硬凑。
9. 输出要像可执行情报报告，不要像普通闲聊总结。

请按以下结构输出：

## 一句话总览
用 1-2 句说明过去 24 小时最重要的变化和风险。

## 数据概览
写明对话数、消息数、最活跃的几个会话、主要主题、明显高优先级事项。

## 外部背景校准
如果有外部背景快照，列出 3-8 条和聊天内容真正相关的外部信息：
- 外部发生了什么。
- 和哪段微信聊天有关。
- 对用户可能意味着什么。
- 置信度：高/中/低。
如果没有可用外部背景或不相关，明确说明“不强行结合”。

## 重点对话详解
按重要程度列出 12-25 个对话。每个对话使用：
### 群/联系人名
- 发生了什么：具体说明。
- 时间线/参与者：尽量写出关键时间和发言人。
- 关键观点/信息：列出具体事实、数字、链接、人物、项目或讨论分歧。
- 证据线索：列 2-4 条“时间 + 发言人 + 关键词/短摘”，方便回查。
- 外部背景对照：如果相关，说明公开信息如何印证/冲突/补充；不相关就写“暂无明显外部对照”。
- 需要你做什么：无需/需关注/需回复/需决策，并给一句建议。

## 主题归纳
按主题归纳，例如：求职、人际、汽车、投资、项目、生活。每个主题写具体内容和来源会话。

## 决策、承诺和待办
像会议纪要一样拆成：
- 已明确决定
- 他人承诺/等待别人
- 你需要做
- 只是可选关注
每条尽量写负责人、对象、时间线和建议下一步。

## 待办和需要回复
列出明确待办、潜在待办、建议回复对象，并给可直接复制的回复草稿。没有也要说“暂无明确必须回复”。

## 风险和机会
列出投资风险、项目风险、法律/合规风险、关系风险、信息机会。对投资相关内容必须提示“不构成投资建议”，并区分聊天观点与公开信息。

## 关系和情绪信号
总结哪些人/群情绪明显、可能需要安抚、推进或保持距离。

## 可检索关键词
给出 10-30 个关键词，方便之后查知识库。

## 明天建议关注
给 3-8 条明天应关注的事项。"""


REPORT_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "投资/市场": (
        "股票", "美股", "港股", "a股", "etf", "期权", "财报", "行情", "涨", "跌",
        "买入", "卖出", "做多", "做空", "仓位", "止损", "止盈", "套利", "币", "btc",
        "eth", "sol", "tesla", "tsla", "nvidia", "nvda", "奈飞", "nflx", "市场",
    ),
    "项目/技术": (
        "项目", "代码", "api", "agent", "模型", "claude", "gpt", "服务", "部署", "重启",
        "数据库", "同步", "知识库", "embedding", "rag", "github", "报错", "接口", "产品",
    ),
    "汽车": (
        "车", "汽车", "比亚迪", "海豹", "特斯拉", "model", "电池", "续航", "堵转",
        "车窗", "轮胎", "维修", "售后", "充电", "智驾",
    ),
    "求职/工作": (
        "求职", "招聘", "面试", "简历", "offer", "工资", "工作", "岗位", "入职",
        "离职", "hr", "投递",
    ),
    "人际/生活": (
        "吃饭", "见面", "聊天", "朋友", "家", "情绪", "焦虑", "孩子", "学习", "生活",
        "旅行", "约", "生日",
    ),
    "风险/异常": (
        "风险", "问题", "不行", "失败", "失效", "没收到", "报错", "亏", "危险",
        "卡住", "延迟", "投诉", "坏了", "异常",
    ),
    "待办/跟进": (
        "帮我", "需要", "记得", "安排", "确认", "回复", "跟进", "处理", "发给",
        "明天", "今天", "今晚", "几点", "todo", "待办", "推进",
    ),
}


def build_daily_report_evidence_pack(
    messages: list[dict],
    max_chats: int = 28,
    max_examples_per_chat: int = 12,
    max_chars: int = 70000,
) -> str:
    """Create a structured evidence pack before asking the model to write the daily report."""
    if not messages:
        return "【日报证据包】没有聊天记录。"

    groups: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    for msg in messages:
        talker = msg.get("talker") or "unknown"
        groups.setdefault(talker, []).append(msg)
        names.setdefault(talker, msg.get("remark") or msg.get("nickname") or msg.get("alias") or talker)

    for items in groups.values():
        items.sort(key=lambda item: (int(item.get("create_time") or 0), int(item.get("id") or 0)))

    total_msgs = sum(len(items) for items in groups.values())
    dates = [msg.get("create_date", "") for msg in messages if msg.get("create_date")]
    date_range = f"{min(dates)} 至 {max(dates)}" if dates else "未知"
    max_time = max((int(msg.get("create_time") or 0) for msg in messages), default=0)

    scored = []
    topic_totals = {topic: 0 for topic in REPORT_TOPIC_KEYWORDS}
    for talker, items in groups.items():
        tag_counts = _report_topic_counts(items)
        for topic, count in tag_counts.items():
            topic_totals[topic] = topic_totals.get(topic, 0) + count
        source_count = sum(1 for item in items if _source_details(item, limit=260))
        last_time = int(items[-1].get("create_time") or 0)
        recent_boost = 0
        if max_time and max_time - last_time < 6 * 3600:
            recent_boost = 35
        elif max_time and max_time - last_time < 18 * 3600:
            recent_boost = 18
        importance_hits = sum(tag_counts.values())
        action_hits = tag_counts.get("待办/跟进", 0)
        risk_hits = tag_counts.get("风险/异常", 0)
        score = len(items) + source_count * 18 + action_hits * 16 + risk_hits * 12 + importance_hits * 6 + recent_boost
        scored.append((score, talker, items, tag_counts, source_count))

    scored.sort(key=lambda item: (item[0], len(item[2]), int(item[2][-1].get("create_time") or 0)), reverse=True)
    topic_line = "；".join(
        f"{topic} {count} 条线索"
        for topic, count in sorted(topic_totals.items(), key=lambda item: item[1], reverse=True)
        if count
    ) or "未命中明显主题词"

    parts = [
        "【日报证据包】",
        f"- 范围：{date_range}",
        f"- 总量：{len(groups)} 个对话，{total_msgs} 条消息",
        f"- 主题命中：{topic_line}",
        "- 使用方式：先依据下面的会话卡片和证据片段写报告；不要只按消息数量排序，要优先待办、风险、投资、项目、链接/图片解析。",
    ]
    char_count = sum(len(part) + 1 for part in parts)

    for index, (score, talker, items, tag_counts, source_count) in enumerate(scored[:max_chats], start=1):
        name = names.get(talker) or talker
        chat_type = "群聊" if int(items[0].get("is_group") or 0) else "私聊"
        first_time = _report_time(items[0].get("create_time"))
        last_time = _report_time(items[-1].get("create_time"))
        tags = [f"{topic}({count})" for topic, count in sorted(tag_counts.items(), key=lambda item: item[1], reverse=True) if count]
        if source_count:
            tags.append(f"链接/图片解析({source_count})")
        reasons = _report_reason_line(tag_counts, source_count, len(items))
        examples = _select_report_examples(items, max_examples_per_chat)

        lines = [
            f"\n### {index}. {name}（{chat_type}，重要度 {int(score)}）",
            f"- 时间/规模：{first_time} 至 {last_time}，{len(items)} 条消息",
            f"- 主题标签：{', '.join(tags) if tags else '普通聊天'}",
            f"- 入选原因：{reasons}",
            "- 证据片段：",
        ]
        for msg in examples:
            speaker = _report_speaker(msg, name, chat_type == "群聊")
            content = _report_message_text(msg, include_source=True)
            lines.append(f"  - {_report_time(msg.get('create_time'))} {speaker}: {_truncate_report_text(content, 260)}")

        section = "\n".join(lines)
        if char_count + len(section) > max_chars:
            parts.append("\n【证据包截断】剩余对话因长度限制未展开，请在原始聊天记录中继续查证。")
            break
        parts.append(section)
        char_count += len(section)

    return "\n".join(parts)


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


def _report_topic_counts(messages: list[dict]) -> dict[str, int]:
    counts = {topic: 0 for topic in REPORT_TOPIC_KEYWORDS}
    for msg in messages:
        text = _report_message_text(msg, include_source=True).lower()
        for topic, keywords in REPORT_TOPIC_KEYWORDS.items():
            if any(keyword.lower() in text for keyword in keywords):
                counts[topic] += 1
    return counts


def _report_reason_line(tag_counts: dict[str, int], source_count: int, message_count: int) -> str:
    reasons = []
    if message_count >= 80:
        reasons.append("消息量高")
    if tag_counts.get("待办/跟进", 0):
        reasons.append("包含可能需要处理的事项")
    if tag_counts.get("投资/市场", 0):
        reasons.append("包含投资/市场线索")
    if tag_counts.get("项目/技术", 0):
        reasons.append("包含项目或技术进展")
    if tag_counts.get("风险/异常", 0):
        reasons.append("出现风险或异常信号")
    if source_count:
        reasons.append("包含可解析的链接或图片")
    if not reasons:
        reasons.append("近期有连续交流")
    return "；".join(reasons)


def _select_report_examples(messages: list[dict], limit: int) -> list[dict]:
    scored = []
    for idx, msg in enumerate(messages):
        text = _report_message_text(msg, include_source=True).lower()
        score = 0
        for topic, keywords in REPORT_TOPIC_KEYWORDS.items():
            if any(keyword.lower() in text for keyword in keywords):
                score += 10
                if topic in {"待办/跟进", "投资/市场", "项目/技术", "风险/异常"}:
                    score += 8
        if _source_details(msg, limit=260):
            score += 18
        if int(msg.get("is_sender") or 0):
            score += 3
        if len(text) >= 40:
            score += 4
        scored.append((score, idx, msg))

    picked: dict[int, dict] = {}
    for score, idx, msg in sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[: max(4, limit - 4)]:
        if score > 0:
            picked[int(msg.get("id") or idx)] = msg
    for msg in messages[-4:]:
        picked[int(msg.get("id") or id(msg))] = msg

    selected = list(picked.values())
    selected.sort(key=lambda item: (int(item.get("create_time") or 0), int(item.get("id") or 0)))
    return selected[:limit]


def _report_message_text(msg: dict, include_source: bool = False) -> str:
    content = (msg.get("content") or msg.get("display_content") or "").strip()
    if not content:
        content = f"[{msg.get('type_name') or '消息'}]"
    content = re.sub(r"\s+", " ", content)
    if include_source:
        details = _source_details(msg, limit=900)
        if details:
            details = re.sub(r"\s+", " ", details)
            content = f"{content} | {details}"
    return content


def _report_speaker(msg: dict, talker_name: str, is_group: bool) -> str:
    if msg.get("is_sender"):
        return "我"
    if is_group and msg.get("sender"):
        return str(msg.get("sender"))
    return talker_name or "对方"


def _report_time(value) -> str:
    try:
        return datetime.fromtimestamp(int(value or 0)).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def _truncate_report_text(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


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
