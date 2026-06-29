from __future__ import annotations

import uuid
from typing import Any

from app.knowledge.embedding import embedding_client
from app.storage.database import AppDatabase


def display_name(item: dict) -> str:
    return item.get("remark") or item.get("nickname") or item.get("alias") or item.get("talker") or ""


def compact_message(item: dict) -> dict[str, Any]:
    content = item.get("content") or item.get("display_content") or f"[{item.get('type_name') or 'message'}]"
    return {
        "id": item.get("id"),
        "talker": item.get("talker", ""),
        "name": display_name(item),
        "sender": item.get("sender", ""),
        "is_sender": int(item.get("is_sender") or 0),
        "date": item.get("create_date", ""),
        "time": item.get("create_time", 0),
        "content": content,
    }


class AgentTools:
    def __init__(self, db: AppDatabase):
        self.db = db

    async def search_conversations(self, query: str, limit: int = 8) -> list[dict]:
        items = await self.db.search_conversations(query, limit=limit)
        return [
            {
                "talker": item.get("talker", ""),
                "name": display_name(item),
                "nickname": item.get("nickname", ""),
                "remark": item.get("remark", ""),
                "alias": item.get("alias", ""),
                "is_group": bool(item.get("is_group")),
                "message_count": item.get("msg_count", 0),
                "last_message": item.get("last_message", ""),
            }
            for item in items
        ]

    async def recent_messages(self, talker: str, limit: int = 80) -> list[dict]:
        items = await self.db.get_recent_messages_for_talker(talker, limit=min(max(limit, 1), 200))
        return [compact_message(item) for item in items]

    async def global_recent_messages(self, hours: int = 0, limit: int = 1000) -> list[dict]:
        bounded_hours = 0 if hours <= 0 else min(hours, 720)
        bounded_limit = 0 if limit <= 0 else min(max(limit, 1), 5000)
        items = await self.db.get_all_recent_messages(hours=bounded_hours, limit=bounded_limit)
        return [compact_message(item) for item in items]

    async def global_message_overview(self) -> dict:
        return await self.db.get_global_message_overview()

    async def search_messages(self, query: str, talker: str = "", limit: int = 50) -> list[dict]:
        items = await self.db.search_messages(query, talker=talker, limit=min(max(limit, 1), 200))
        return [compact_message(item) for item in items]

    async def search_knowledge(self, query: str, talker: str = "", limit: int = 8) -> list[dict]:
        bounded_limit = min(max(limit, 1), 20)
        items = await self.db.search_knowledge(query, talker=talker, limit=bounded_limit)
        if embedding_client.configured:
            try:
                vectors = await embedding_client.embed_texts([query])
                vector_items = await self.db.search_knowledge_vector(
                    vectors[0],
                    model=embedding_client.model,
                    talker=talker,
                    limit=bounded_limit,
                )
                merged = {int(item.get("id") or 0): item for item in items}
                for item in vector_items:
                    merged.setdefault(int(item.get("id") or 0), item)
                items = sorted(
                    merged.values(),
                    key=lambda item: (
                        1 if item.get("retrieval") == "embedding" else 0,
                        float(item.get("score") or 0),
                        int(item.get("end_time") or 0),
                    ),
                    reverse=True,
                )[:bounded_limit]
            except Exception:
                pass
        out = []
        for item in items:
            metadata = item.get("metadata") or {}
            out.append(
                {
                    "id": item.get("id"),
                    "talker": item.get("talker", ""),
                    "name": metadata.get("name") or item.get("title") or item.get("talker", ""),
                    "title": item.get("title", ""),
                    "text": item.get("text", ""),
                    "start_message_id": item.get("start_message_id", 0),
                    "end_message_id": item.get("end_message_id", 0),
                    "start_time": item.get("start_time", 0),
                    "end_time": item.get("end_time", 0),
                    "message_count": item.get("message_count", 0),
                    "score": item.get("score", 0),
                }
            )
        return out

    async def create_send_confirmation(self, contact_name: str, content: str) -> dict:
        action_id = str(uuid.uuid4())[:8]
        return await self.db.create_pending_action(
            action_id,
            "send_wechat_text",
            {"contact_name": contact_name.strip(), "content": content.strip()},
        )
