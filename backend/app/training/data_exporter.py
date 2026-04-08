"""Export WeChat chat records to LLaMA-Factory training format.

Extracts all conversations where the user participated (is_sender=1),
formats them as multi-turn dialogues in ShareGPT format.
"""
import json
import os
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.config.settings import get_settings

# 30 minutes gap = new conversation
CONVERSATION_GAP_SECONDS = 30 * 60


async def export_training_data(db) -> dict:
    """Export all user's chat records as LLaMA-Factory training data.

    Returns dict with stats and output file path.
    """
    settings = get_settings()
    output_dir = Path(settings.DATA_DIR) / "training"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "my_wechat_style.json"
    dataset_info_file = output_dir / "dataset_info.json"

    # Get all talkers where user has sent messages
    rows = await db._db.execute_fetchall(
        "SELECT DISTINCT talker FROM messages WHERE is_sender = 1"
    )
    talkers = [r["talker"] for r in rows]
    logger.info(f"Found {len(talkers)} conversations with user messages")

    all_conversations = []
    total_my_msgs = 0
    total_other_msgs = 0

    for talker in talkers:
        # Get all text messages for this conversation
        msgs = await db._db.execute_fetchall(
            """SELECT is_sender, sender, content, create_time, type
               FROM messages
               WHERE talker = ? AND type = 1 AND content != '' AND content NOT LIKE '[%'
               ORDER BY create_time ASC""",
            (talker,),
        )
        if not msgs:
            continue

        messages = [dict(m) for m in msgs]

        # Split into conversation segments (30 min gap)
        segments = _split_conversations(messages)

        for segment in segments:
            conv = _build_conversation(segment)
            if conv:
                all_conversations.append(conv)
                for turn in conv["conversations"]:
                    if turn["from"] == "gpt":
                        total_my_msgs += 1
                    else:
                        total_other_msgs += 1

    # Write training data
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_conversations, f, ensure_ascii=False, indent=2)

    # Write dataset_info.json for LLaMA-Factory
    dataset_info = {
        "my_wechat_style": {
            "file_name": "my_wechat_style.json",
            "formatting": "sharegpt",
            "columns": {
                "messages": "conversations",
            },
            "tags": {
                "role_tag": "from",
                "content_tag": "value",
                "user_tag": "human",
                "assistant_tag": "gpt",
            },
        }
    }
    with open(dataset_info_file, "w", encoding="utf-8") as f:
        json.dump(dataset_info, f, ensure_ascii=False, indent=2)

    stats = {
        "total_conversations": len(all_conversations),
        "total_my_messages": total_my_msgs,
        "total_other_messages": total_other_msgs,
        "total_talkers": len(talkers),
        "output_file": str(output_file),
        "dataset_info_file": str(dataset_info_file),
        "file_size_mb": round(os.path.getsize(output_file) / 1024 / 1024, 2),
    }
    logger.info(f"Exported training data: {stats}")
    return stats


def _split_conversations(messages: list[dict]) -> list[list[dict]]:
    """Split messages into conversation segments by time gap."""
    if not messages:
        return []

    segments = []
    current = [messages[0]]

    for i in range(1, len(messages)):
        gap = messages[i]["create_time"] - messages[i - 1]["create_time"]
        if gap > CONVERSATION_GAP_SECONDS:
            if len(current) >= 2:  # Need at least 2 messages
                segments.append(current)
            current = []
        current.append(messages[i])

    if len(current) >= 2:
        segments.append(current)

    return segments


def _build_conversation(messages: list[dict]) -> dict | None:
    """Build a ShareGPT conversation from a message segment.

    is_sender=1 → "gpt" (the style we want to learn)
    is_sender=0 → "human" (the other person)

    Rules:
    - Must start with "human" turn
    - Consecutive same-role messages are merged
    - Must have at least 1 gpt turn
    """
    turns = []
    has_gpt = False

    for msg in messages:
        content = msg["content"].strip()
        if not content:
            continue

        role = "gpt" if msg["is_sender"] else "human"
        if role == "gpt":
            has_gpt = True

        # Merge consecutive same-role messages
        if turns and turns[-1]["from"] == role:
            turns[-1]["value"] += "\n" + content
        else:
            turns.append({"from": role, "value": content})

    if not has_gpt or not turns:
        return None

    # Ensure starts with "human"
    if turns[0]["from"] == "gpt":
        turns.insert(0, {"from": "human", "value": "[对方发来消息]"})

    # Ensure alternating pattern (LLaMA-Factory requirement)
    cleaned = []
    for turn in turns:
        if cleaned and cleaned[-1]["from"] == turn["from"]:
            cleaned[-1]["value"] += "\n" + turn["value"]
        else:
            cleaned.append(turn)

    # Must have at least one human + one gpt
    has_h = any(t["from"] == "human" for t in cleaned)
    has_g = any(t["from"] == "gpt" for t in cleaned)
    if not (has_h and has_g) or len(cleaned) < 2:
        return None

    return {"conversations": cleaned}


async def get_export_stats(db) -> dict:
    """Get stats about available training data without exporting."""
    total = await db._db.execute_fetchall(
        "SELECT COUNT(*) as cnt FROM messages WHERE is_sender = 1 AND type = 1"
    )
    my_msgs = total[0]["cnt"] if total else 0

    total_other = await db._db.execute_fetchall(
        "SELECT COUNT(*) as cnt FROM messages WHERE is_sender = 0 AND type = 1"
    )
    other_msgs = total_other[0]["cnt"] if total_other else 0

    talkers = await db._db.execute_fetchall(
        "SELECT COUNT(DISTINCT talker) as cnt FROM messages WHERE is_sender = 1"
    )
    talker_count = talkers[0]["cnt"] if talkers else 0

    output_file = Path(get_settings().DATA_DIR) / "training" / "my_wechat_style.json"
    exported = output_file.exists()
    file_size = round(os.path.getsize(output_file) / 1024 / 1024, 2) if exported else 0

    return {
        "my_text_messages": my_msgs,
        "other_text_messages": other_msgs,
        "conversation_count": talker_count,
        "already_exported": exported,
        "file_size_mb": file_size,
    }
