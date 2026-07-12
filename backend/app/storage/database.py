import json
import uuid
from datetime import datetime
from typing import Optional

import aiosqlite
from loguru import logger

from app.knowledge.embedding import dot_score, pack_vector, unpack_vector
from app.storage.migrations import SCHEMA_SQL


class AppDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._ensure_chat_api_columns()
        await self._db.commit()
        logger.info(f"Database connected: {self.db_path}")

    async def close(self):
        if self._db:
            await self._db.close()
            logger.info("Database closed")

    async def _ensure_chat_api_columns(self):
        rows = await self._db.execute_fetchall("PRAGMA table_info(chat_apis)")
        existing = {str(row["name"]) for row in rows}
        additions = {
            "scope": "ALTER TABLE chat_apis ADD COLUMN scope TEXT DEFAULT 'records'",
            "permissions": "ALTER TABLE chat_apis ADD COLUMN permissions TEXT DEFAULT 'records:read'",
            "last_used_at": "ALTER TABLE chat_apis ADD COLUMN last_used_at DATETIME",
        }
        for column, statement in additions.items():
            if column not in existing:
                await self._db.execute(statement)
        await self._db.execute(
            """UPDATE chat_apis
               SET scope = COALESCE(NULLIF(scope, ''), 'records'),
                   permissions = COALESCE(NULLIF(permissions, ''), 'records:read')
               WHERE scope IS NULL OR scope = '' OR permissions IS NULL OR permissions = ''"""
        )

    # --- Contacts ---

    async def bulk_upsert_contacts(self, contacts: list[dict]) -> int:
        if not contacts:
            return 0
        await self._db.executemany(
            """INSERT INTO contacts (username, nickname, remark, alias, is_group, updated_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(username) DO UPDATE SET
                 nickname=excluded.nickname, remark=excluded.remark,
                 alias=excluded.alias, is_group=excluded.is_group,
                 updated_at=CURRENT_TIMESTAMP""",
            [
                (
                    c.get("username", ""),
                    c.get("nickname", ""),
                    c.get("remark", ""),
                    c.get("alias", ""),
                    c.get("is_group", 0),
                )
                for c in contacts
            ],
        )
        await self._db.commit()
        return len(contacts)

    async def get_contacts(self, search: str = "", contact_type: str = "all",
                           limit: int = 100, offset: int = 0) -> list[dict]:
        conditions = []
        params = []
        if search:
            conditions.append("(nickname LIKE ? OR remark LIKE ? OR username LIKE ?)")
            params.extend([f"%{search}%"] * 3)
        if contact_type == "friend":
            conditions.append("is_group = 0")
        elif contact_type == "group":
            conditions.append("is_group = 1")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = await self._db.execute_fetchall(
            f"SELECT * FROM contacts {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        return [dict(r) for r in rows]

    async def get_contact_count(self) -> int:
        rows = await self._db.execute_fetchall("SELECT COUNT(*) AS count FROM contacts")
        return int(rows[0]["count"] or 0) if rows else 0

    # --- Messages ---

    async def bulk_insert_messages(self, messages: list[dict]) -> int:
        if not messages:
            return 0
        cursor = await self._db.executemany(
            """INSERT OR IGNORE INTO messages
               (wechat_local_id, msg_svr_id, talker, sender, type, type_name,
                is_sender, content, display_content, create_time, create_date, is_group)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    msg.get("wechat_local_id"),
                    msg.get("msg_svr_id"),
                    msg.get("talker", ""),
                    msg.get("sender", ""),
                    msg.get("type", 1),
                    msg.get("type_name", "text"),
                    msg.get("is_sender", 0),
                    msg.get("content", ""),
                    msg.get("display_content", ""),
                    msg.get("create_time", 0),
                    msg.get("create_date", ""),
                    msg.get("is_group", 0),
                )
                for msg in messages
            ],
        )
        await self._db.commit()
        return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

    async def get_message_count(self) -> int:
        rows = await self._db.execute_fetchall("SELECT COUNT(*) AS count FROM messages")
        return int(rows[0]["count"] or 0) if rows else 0

    async def get_messages(self, talker: str = "", date: str = "",
                           search: str = "", page: int = 1,
                           page_size: int = 50) -> dict:
        conditions = []
        params = []
        if talker:
            conditions.append("m.talker = ?")
            params.append(talker)
        if date:
            conditions.append("m.create_date = ?")
            params.append(date)
        if search:
            conditions.append("m.content LIKE ?")
            params.append(f"%{search}%")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_row = await self._db.execute_fetchall(
            f"SELECT COUNT(*) as cnt FROM messages m {where}", params
        )
        total = count_row[0]["cnt"] if count_row else 0

        offset = (page - 1) * page_size
        rows = await self._db.execute_fetchall(
            f"""SELECT m.*, c.nickname, c.remark
                FROM messages m
                LEFT JOIN contacts c ON m.talker = c.username
                {where}
                ORDER BY m.create_time ASC
                LIMIT ? OFFSET ?""",
            params + [page_size, offset],
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [dict(r) for r in rows],
        }

    async def get_conversations(self, search: str = "") -> list[dict]:
        search_clause = ""
        params = []
        if search:
            search_clause = (
                "HAVING m.talker LIKE ? OR c.nickname LIKE ? OR c.remark LIKE ? "
                "OR c.alias LIKE ? OR last_message LIKE ?"
            )
            params = [f"%{search}%"] * 5
        rows = await self._db.execute_fetchall(
            f"""SELECT m.talker, c.nickname, c.remark, c.is_group,
                       COUNT(*) as msg_count,
                       MAX(m.create_time) as last_time,
                       (SELECT COALESCE(NULLIF(content, ''), '[' || type_name || ']')
                        FROM messages WHERE talker = m.talker
                        ORDER BY create_time DESC LIMIT 1) as last_message,
                       (SELECT type_name FROM messages WHERE talker = m.talker
                        ORDER BY create_time DESC LIMIT 1) as last_type_name
                FROM messages m
                LEFT JOIN contacts c ON m.talker = c.username
                GROUP BY m.talker
                {search_clause}
                ORDER BY last_time DESC""",
            params,
        )
        return [dict(r) for r in rows]

    async def search_conversations(self, search: str, limit: int = 10) -> list[dict]:
        """Resolve a human name or keyword to likely conversations."""
        if not search.strip():
            return []
        rows = await self._db.execute_fetchall(
            """SELECT m.talker, c.nickname, c.remark, c.alias, c.is_group,
                      COUNT(*) AS msg_count,
                      MAX(m.create_time) AS last_time,
                      (SELECT COALESCE(NULLIF(content, ''), '[' || type_name || ']')
                       FROM messages WHERE talker = m.talker
                       ORDER BY create_time DESC LIMIT 1) AS last_message
               FROM messages m
               LEFT JOIN contacts c ON m.talker = c.username
               GROUP BY m.talker
               HAVING m.talker LIKE ? OR c.nickname LIKE ? OR c.remark LIKE ?
                  OR c.alias LIKE ? OR last_message LIKE ?
               ORDER BY
                 CASE
                   WHEN c.remark = ? THEN 0
                   WHEN c.nickname = ? THEN 1
                   WHEN c.alias = ? THEN 2
                   WHEN m.talker = ? THEN 3
                   ELSE 4
                 END,
                 last_time DESC
               LIMIT ?""",
            [f"%{search}%"] * 5 + [search, search, search, search, limit],
        )
        return [dict(r) for r in rows]

    async def search_messages(self, search: str, talker: str = "", limit: int = 50) -> list[dict]:
        if not search.strip():
            return []
        conditions = ["(m.content LIKE ? OR m.display_content LIKE ?)"]
        params: list = [f"%{search}%", f"%{search}%"]
        if talker:
            conditions.append("m.talker = ?")
            params.append(talker)
        where = " AND ".join(conditions)
        rows = await self._db.execute_fetchall(
            f"""SELECT m.id, m.talker, m.sender, m.type_name, m.is_sender,
                      m.content, m.display_content, m.create_time, m.create_date,
                      c.nickname, c.remark, c.is_group
               FROM messages m
               LEFT JOIN contacts c ON m.talker = c.username
               WHERE {where}
               ORDER BY m.create_time DESC
               LIMIT ?""",
            params + [limit],
        )
        return [dict(r) for r in rows]

    async def get_new_inbound_messages(self, talker: str, after_id: int, limit: int = 20) -> list[dict]:
        rows = await self._db.execute_fetchall(
            """SELECT m.id, m.talker, m.sender, m.type_name, m.is_sender,
                      m.content, m.display_content, m.create_time, m.create_date,
                      c.nickname, c.remark, c.is_group
               FROM messages m
               LEFT JOIN contacts c ON m.talker = c.username
               WHERE m.talker = ? AND m.id > ? AND m.is_sender = 0
               ORDER BY m.id ASC
               LIMIT ?""",
            (talker, after_id, limit),
        )
        return [dict(r) for r in rows]

    async def get_recent_messages_for_talker(self, talker: str, limit: int = 50) -> list[dict]:
        rows = await self._db.execute_fetchall(
            """SELECT m.*, c.nickname, c.remark
               FROM messages m
               LEFT JOIN contacts c ON m.talker = c.username
               WHERE m.talker = ?
               ORDER BY m.create_time DESC
               LIMIT ?""",
            (talker, limit),
        )
        return [dict(r) for r in reversed(rows)]

    async def get_all_messages_for_talker(self, talker: str, max_messages: int = 50000) -> list[dict]:
        """Load messages for a talker (up to max_messages most recent), ordered chronologically."""
        rows = await self._db.execute_fetchall(
            """SELECT m.talker, m.sender, m.type, m.type_name, m.is_sender,
                      m.content, m.create_time, m.create_date, m.is_group,
                      c.nickname, c.remark
               FROM messages m
               LEFT JOIN contacts c ON m.talker = c.username
               WHERE m.talker = ?
               ORDER BY m.create_time DESC
               LIMIT ?""",
            (talker, max_messages),
        )
        return [dict(r) for r in reversed(rows)]

    async def get_all_messages(self, limit: int = 0) -> list[dict]:
        """Load messages across all conversations.

        When limit is <= 0, load the full synchronized history. When limit is
        positive, load the latest N messages and return them chronologically.
        """
        if limit and limit > 0:
            rows = await self._db.execute_fetchall(
                """SELECT * FROM (
                       SELECT m.id, m.wechat_local_id, m.talker, m.sender, m.type, m.type_name, m.is_sender,
                              m.content, m.display_content, m.create_time, m.create_date, m.is_group,
                              c.nickname, c.remark
                       FROM messages m
                       LEFT JOIN contacts c ON m.talker = c.username
                       ORDER BY m.create_time DESC
                       LIMIT ?
                   )
                   ORDER BY create_time ASC""",
                (limit,),
            )
            return [dict(r) for r in rows]

        rows = await self._db.execute_fetchall(
            """SELECT m.id, m.wechat_local_id, m.talker, m.sender, m.type, m.type_name, m.is_sender,
                      m.content, m.display_content, m.create_time, m.create_date, m.is_group,
                      c.nickname, c.remark
               FROM messages m
               LEFT JOIN contacts c ON m.talker = c.username
               ORDER BY m.create_time ASC"""
        )
        return [dict(r) for r in rows]

    async def get_global_message_overview(self) -> dict:
        """Return aggregate coverage for all synchronized messages."""
        totals = await self._db.execute_fetchall(
            """SELECT COUNT(*) AS total_messages,
                      COUNT(DISTINCT talker) AS total_conversations,
                      MIN(create_date) AS first_date,
                      MAX(create_date) AS last_date,
                      MIN(create_time) AS first_time,
                      MAX(create_time) AS last_time
               FROM messages"""
        )
        conversations = await self._db.execute_fetchall(
            """SELECT m.talker, c.nickname, c.remark, c.is_group,
                      COUNT(*) AS msg_count,
                      MIN(m.create_date) AS first_date,
                      MAX(m.create_date) AS last_date,
                      MAX(m.create_time) AS last_time,
                      (SELECT COALESCE(NULLIF(content, ''), '[' || type_name || ']')
                       FROM messages
                       WHERE talker = m.talker
                       ORDER BY create_time DESC
                       LIMIT 1) AS last_message
               FROM messages m
               LEFT JOIN contacts c ON m.talker = c.username
               GROUP BY m.talker
               ORDER BY msg_count DESC
               LIMIT 50"""
        )
        dates = await self._db.execute_fetchall(
            """SELECT create_date AS date, COUNT(*) AS count
               FROM messages
               GROUP BY create_date
               ORDER BY create_date ASC"""
        )
        return {
            "totals": dict(totals[0]) if totals else {},
            "top_conversations": [dict(r) for r in conversations],
            "date_counts": [dict(r) for r in dates],
        }

    async def get_all_recent_messages(self, hours: int = 24, limit: int = 5000) -> list[dict]:
        """Load messages across all conversations.

        hours <= 0 means full synchronized history. limit <= 0 means no row
        limit for that full-history read.
        """
        if hours <= 0:
            return await self.get_all_messages(limit=limit)

        import time
        since_ts = int(time.time()) - hours * 3600
        rows = await self._db.execute_fetchall(
            """SELECT m.id, m.wechat_local_id, m.talker, m.sender, m.type, m.type_name, m.is_sender,
                      m.content, m.display_content, m.create_time, m.create_date, m.is_group,
                      c.nickname, c.remark
               FROM messages m
               LEFT JOIN contacts c ON m.talker = c.username
               WHERE m.create_time > ?
               ORDER BY m.create_time ASC
               LIMIT ?""",
            (since_ts, limit),
        )
        return [dict(r) for r in rows]

    async def attach_source_enrichments(self, messages: list[dict]) -> list[dict]:
        if not messages:
            return messages
        ids = [int(item.get("id") or 0) for item in messages if int(item.get("id") or 0)]
        if not ids:
            return messages
        placeholders = ",".join("?" for _ in ids)
        rows = await self._db.execute_fetchall(
            f"""SELECT message_id, kind, source_key, extracted_text, metadata, status, error
                FROM message_source_enrichments
                WHERE message_id IN ({placeholders})
                ORDER BY id ASC""",
            ids,
        )
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except json.JSONDecodeError:
                item["metadata"] = {}
            grouped.setdefault(int(item.get("message_id") or 0), []).append(item)
        for item in messages:
            item["source_enrichments"] = grouped.get(int(item.get("id") or 0), [])
        return messages

    async def get_source_enrichment(self, message_id: int, kind: str, source_key: str) -> dict | None:
        rows = await self._db.execute_fetchall(
            """SELECT * FROM message_source_enrichments
               WHERE message_id = ? AND kind = ? AND source_key = ?
               LIMIT 1""",
            (message_id, kind, source_key),
        )
        if not rows:
            return None
        item = dict(rows[0])
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        return item

    async def upsert_source_enrichment(
        self,
        *,
        message_id: int,
        kind: str,
        source_key: str,
        extracted_text: str,
        metadata: dict | None = None,
        status: str = "ok",
        error: str = "",
    ) -> dict:
        await self._db.execute(
            """INSERT INTO message_source_enrichments
               (message_id, kind, source_key, extracted_text, metadata, status, error, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(message_id, kind, source_key) DO UPDATE SET
                 extracted_text=excluded.extracted_text,
                 metadata=excluded.metadata,
                 status=excluded.status,
                 error=excluded.error,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                message_id,
                kind,
                source_key,
                extracted_text,
                json.dumps(metadata or {}, ensure_ascii=False),
                status,
                error,
            ),
        )
        await self._db.commit()
        return {
            "message_id": message_id,
            "kind": kind,
            "source_key": source_key,
            "extracted_text": extracted_text,
            "metadata": metadata or {},
            "status": status,
            "error": error,
        }

    # --- Timeline ---

    async def get_message_dates(self, talker: str) -> list[dict]:
        """Get all dates with messages for a talker, with message count per date."""
        rows = await self._db.execute_fetchall(
            """SELECT create_date as date, COUNT(*) as count
               FROM messages
               WHERE talker = ?
               GROUP BY create_date
               ORDER BY create_date ASC""",
            (talker,),
        )
        return [dict(r) for r in rows]

    async def get_messages_by_date(self, talker: str, date: str, page_size: int = 100) -> dict:
        """Get messages for a talker starting from a specific date."""
        # Count messages before this date (for offset calculation)
        count_before = await self._db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM messages WHERE talker = ? AND create_date < ?",
            (talker, date),
        )
        offset = count_before[0]["cnt"] if count_before else 0

        # Get messages from this date onwards
        rows = await self._db.execute_fetchall(
            """SELECT m.*, c.nickname, c.remark
               FROM messages m
               LEFT JOIN contacts c ON m.talker = c.username
               WHERE m.talker = ? AND m.create_date >= ?
               ORDER BY m.create_time ASC
               LIMIT ?""",
            (talker, date, page_size),
        )

        # Get total count
        total_row = await self._db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM messages WHERE talker = ?",
            (talker,),
        )
        total = total_row[0]["cnt"] if total_row else 0

        return {
            "total": total,
            "offset": offset,
            "items": [dict(r) for r in rows],
        }

    # --- Sync State ---

    # --- Chat APIs ---

    async def create_chat_api(
        self,
        api_id: str,
        talker: str,
        api_key: str,
        name: str = "",
        scope: str = "records",
        permissions: str = "records:read",
    ) -> dict:
        await self._db.execute(
            """INSERT INTO chat_apis (id, talker, api_key, name, scope, permissions)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (api_id, talker, api_key, name, scope, permissions),
        )
        await self._db.commit()
        return {
            "id": api_id,
            "talker": talker,
            "api_key": api_key,
            "name": name,
            "scope": scope,
            "permissions": permissions,
            "enabled": 1,
        }

    async def list_chat_apis(self) -> list[dict]:
        rows = await self._db.execute_fetchall(
            """SELECT a.*, c.nickname, c.remark, c.is_group
               FROM chat_apis a
               LEFT JOIN contacts c ON a.talker = c.username
               ORDER BY a.created_at DESC"""
        )
        return [dict(r) for r in rows]

    async def get_chat_api_by_key(self, api_key: str) -> dict | None:
        rows = await self._db.execute_fetchall(
            "SELECT * FROM chat_apis WHERE api_key = ? AND enabled = 1", (api_key,)
        )
        return dict(rows[0]) if rows else None

    async def touch_chat_api(self, api_id: str):
        await self._db.execute(
            "UPDATE chat_apis SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
            (api_id,),
        )
        await self._db.commit()

    async def delete_chat_api(self, api_id: str) -> bool:
        cursor = await self._db.execute("DELETE FROM chat_apis WHERE id = ?", (api_id,))
        await self._db.commit()
        return (cursor.rowcount or 0) > 0

    async def toggle_chat_api(self, api_id: str) -> dict | None:
        await self._db.execute(
            "UPDATE chat_apis SET enabled = CASE WHEN enabled=1 THEN 0 ELSE 1 END WHERE id = ?",
            (api_id,),
        )
        await self._db.commit()
        rows = await self._db.execute_fetchall("SELECT * FROM chat_apis WHERE id = ?", (api_id,))
        return dict(rows[0]) if rows else None

    async def get_sync_state(self) -> dict:
        rows = await self._db.execute_fetchall("SELECT * FROM sync_state WHERE id = 1")
        return dict(rows[0]) if rows else {}

    async def update_sync_state(self, last_timestamp: int, msg_count: int):
        await self._db.execute(
            """UPDATE sync_state SET
                 last_sync_timestamp = ?, last_sync_at = CURRENT_TIMESTAMP,
                 last_msg_count = ?, total_messages = total_messages + ?
               WHERE id = 1""",
            (last_timestamp, msg_count, msg_count),
        )
        await self._db.commit()

    # --- AI Sessions ---

    async def create_ai_session(self, talker: str = "", title: str = "") -> str:
        session_id = str(uuid.uuid4())[:8]
        await self._db.execute(
            "INSERT INTO ai_sessions (id, talker, title) VALUES (?, ?, ?)",
            (session_id, talker, title or f"AI Chat - {datetime.now().strftime('%H:%M')}"),
        )
        await self._db.commit()
        return session_id

    async def get_ai_sessions(self, talker: str = "") -> list[dict]:
        if talker:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM ai_sessions WHERE talker = ? ORDER BY updated_at DESC",
                (talker,),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM ai_sessions ORDER BY updated_at DESC"
            )
        return [dict(r) for r in rows]

    async def get_ai_session(self, session_id: str) -> dict | None:
        rows = await self._db.execute_fetchall(
            "SELECT * FROM ai_sessions WHERE id = ?",
            (session_id,),
        )
        return dict(rows[0]) if rows else None

    async def save_ai_message(self, session_id: str, role: str, content: str):
        await self._db.execute(
            "INSERT INTO ai_messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        await self._db.execute(
            "UPDATE ai_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        await self._db.commit()

    async def get_ai_messages(self, session_id: str) -> list[dict]:
        rows = await self._db.execute_fetchall(
            "SELECT * FROM ai_messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        )
        return [dict(r) for r in rows]

    async def delete_ai_session(self, session_id: str) -> bool:
        await self._db.execute("DELETE FROM ai_messages WHERE session_id = ?", (session_id,))
        cursor = await self._db.execute("DELETE FROM ai_sessions WHERE id = ?", (session_id,))
        await self._db.commit()
        return (cursor.rowcount or 0) > 0

    # --- Settings ---

    async def get_settings(self) -> dict[str, str]:
        rows = await self._db.execute_fetchall("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}

    async def set_setting(self, key: str, value: str, description: str = ""):
        await self._db.execute(
            """INSERT INTO settings (key, value, description, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
            (key, value, description),
        )
        await self._db.commit()

    async def get_setting(self, key: str, default: str = "") -> str:
        rows = await self._db.execute_fetchall("SELECT value FROM settings WHERE key = ?", (key,))
        return str(rows[0]["value"]) if rows else default

    async def create_pending_action(self, action_id: str, action_type: str, payload: dict) -> dict:
        payload_text = json.dumps(payload, ensure_ascii=False)
        await self._db.execute(
            """INSERT INTO agent_pending_actions (id, action_type, payload, status)
               VALUES (?, ?, ?, 'pending')""",
            (action_id, action_type, payload_text),
        )
        await self._db.commit()
        return {
            "id": action_id,
            "action_type": action_type,
            "payload": payload,
            "status": "pending",
        }

    async def get_pending_action(self, action_id: str) -> dict | None:
        rows = await self._db.execute_fetchall(
            "SELECT * FROM agent_pending_actions WHERE id = ?", (action_id,)
        )
        if not rows:
            return None
        item = dict(rows[0])
        try:
            item["payload"] = json.loads(item.get("payload") or "{}")
        except json.JSONDecodeError:
            item["payload"] = {}
        return item

    async def list_pending_actions(self, limit: int = 20) -> list[dict]:
        rows = await self._db.execute_fetchall(
            """SELECT * FROM agent_pending_actions
               WHERE status = 'pending'
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        )
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload") or "{}")
            except json.JSONDecodeError:
                item["payload"] = {}
            out.append(item)
        return out

    async def update_pending_action_status(self, action_id: str, status: str) -> bool:
        cursor = await self._db.execute(
            """UPDATE agent_pending_actions
               SET status = ?, confirmed_at = CASE WHEN ? = 'confirmed' THEN CURRENT_TIMESTAMP ELSE confirmed_at END
               WHERE id = ? AND status = 'pending'""",
            (status, status, action_id),
        )
        await self._db.commit()
        return (cursor.rowcount or 0) > 0

    async def add_agent_audit(self, event_type: str, payload: dict | str = ""):
        payload_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        await self._db.execute(
            "INSERT INTO agent_audit_log (event_type, payload) VALUES (?, ?)",
            (event_type, payload_text),
        )
        await self._db.commit()

    # --- Knowledge Base / RAG ---

    async def get_message_id_bounds(self) -> dict:
        rows = await self._db.execute_fetchall(
            "SELECT MIN(id) AS min_id, MAX(id) AS max_id, COUNT(*) AS count FROM messages"
        )
        return dict(rows[0]) if rows else {"min_id": 0, "max_id": 0, "count": 0}

    async def get_messages_after_id(self, after_id: int = 0, limit: int = 5000) -> list[dict]:
        rows = await self._db.execute_fetchall(
            """SELECT m.id, m.wechat_local_id, m.talker, m.sender, m.type, m.type_name, m.is_sender,
                      m.content, m.display_content, m.create_time, m.create_date,
                      m.is_group, c.nickname, c.remark, c.alias
               FROM messages m
               LEFT JOIN contacts c ON m.talker = c.username
               WHERE m.id > ?
               ORDER BY m.id ASC
               LIMIT ?""",
            (after_id, limit),
        )
        return [dict(r) for r in rows]

    async def clear_knowledge_chunks(self):
        await self._db.execute("DELETE FROM knowledge_embeddings")
        await self._db.execute("DELETE FROM knowledge_chunks_fts")
        await self._db.execute("DELETE FROM knowledge_chunks")
        await self.set_setting("knowledge.last_indexed_message_id", "0", "Knowledge index high-water mark")
        await self.set_setting("knowledge.last_embedded_chunk_id", "0", "Knowledge embedding high-water mark")
        await self._db.commit()

    async def insert_knowledge_chunks(self, chunks: list[dict]) -> int:
        if not chunks:
            return 0
        inserted = 0
        for chunk in chunks:
            metadata = chunk.get("metadata") or {}
            cursor = await self._db.execute(
                """INSERT INTO knowledge_chunks
                   (source, talker, title, text, start_message_id, end_message_id,
                    start_time, end_time, message_count, metadata, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    chunk.get("source", "wechat"),
                    chunk.get("talker", ""),
                    chunk.get("title", ""),
                    chunk.get("text", ""),
                    int(chunk.get("start_message_id") or 0),
                    int(chunk.get("end_message_id") or 0),
                    int(chunk.get("start_time") or 0),
                    int(chunk.get("end_time") or 0),
                    int(chunk.get("message_count") or 0),
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            rowid = cursor.lastrowid
            await self._db.execute(
                "INSERT INTO knowledge_chunks_fts(rowid, title, text, talker) VALUES (?, ?, ?, ?)",
                (rowid, chunk.get("title", ""), chunk.get("text", ""), chunk.get("talker", "")),
            )
            inserted += 1
        await self._db.commit()
        return inserted

    async def get_knowledge_status(self) -> dict:
        chunk_rows = await self._db.execute_fetchall("SELECT COUNT(*) AS count FROM knowledge_chunks")
        embedding_rows = await self._db.execute_fetchall(
            """SELECT COUNT(*) AS count,
                      COUNT(DISTINCT chunk_id) AS embedded_chunks,
                      MAX(chunk_id) AS max_embedded_chunk_id
               FROM knowledge_embeddings"""
        )
        model_rows = await self._db.execute_fetchall(
            """SELECT model,
                      COUNT(*) AS rows,
                      COUNT(DISTINCT chunk_id) AS embedded_chunks,
                      MAX(chunk_id) AS max_embedded_chunk_id
               FROM knowledge_embeddings
               GROUP BY model
               ORDER BY rows DESC"""
        )
        bounds = await self.get_message_id_bounds()
        last_indexed = await self.get_setting("knowledge.last_indexed_message_id", "0")
        last_embedded = await self.get_setting("knowledge.last_embedded_chunk_id", "0")
        latest_chunk = await self._db.execute_fetchall(
            """SELECT id, title, end_message_id, end_time, updated_at
               FROM knowledge_chunks
               ORDER BY id DESC
               LIMIT 1"""
        )
        chunk_count = int(chunk_rows[0]["count"] or 0) if chunk_rows else 0
        embedded_count = int(embedding_rows[0]["embedded_chunks"] or 0) if embedding_rows else 0
        return {
            "chunks": chunk_count,
            "embedded_chunks": embedded_count,
            "embedding_rows": int(embedding_rows[0]["count"] or 0) if embedding_rows else 0,
            "embedding_models": [dict(row) for row in model_rows],
            "last_embedded_chunk_id": int(last_embedded or 0),
            "embeddings_caught_up": chunk_count > 0 and embedded_count >= chunk_count,
            "messages": int(bounds.get("count") or 0),
            "min_message_id": int(bounds.get("min_id") or 0),
            "max_message_id": int(bounds.get("max_id") or 0),
            "last_indexed_message_id": int(last_indexed or 0),
            "caught_up": int(last_indexed or 0) >= int(bounds.get("max_id") or 0),
            "latest_chunk": dict(latest_chunk[0]) if latest_chunk else None,
        }

    async def get_chunks_without_embedding(self, model: str, limit: int = 100) -> list[dict]:
        rows = await self._db.execute_fetchall(
            """SELECT k.id, k.title, k.text, k.talker, k.end_time
               FROM knowledge_chunks k
               LEFT JOIN knowledge_embeddings e
                 ON e.chunk_id = k.id AND e.model = ?
               WHERE e.chunk_id IS NULL
               ORDER BY k.id ASC
               LIMIT ?""",
            (model, min(max(limit, 1), 1000)),
        )
        return [dict(r) for r in rows]

    async def insert_knowledge_embeddings(self, model: str, records: list[dict]) -> int:
        if not records:
            return 0
        rows = []
        max_chunk_id = 0
        for item in records:
            vector = item.get("vector") or []
            chunk_id = int(item.get("chunk_id") or 0)
            if not chunk_id or not vector:
                continue
            max_chunk_id = max(max_chunk_id, chunk_id)
            rows.append((chunk_id, model, len(vector), pack_vector(vector)))
        if not rows:
            return 0
        await self._db.executemany(
            """INSERT OR REPLACE INTO knowledge_embeddings
               (chunk_id, model, dimensions, vector, created_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            rows,
        )
        await self.set_setting("knowledge.last_embedded_chunk_id", str(max_chunk_id), "Knowledge embedding high-water mark")
        await self._db.commit()
        return len(rows)

    async def search_knowledge_vector(
        self,
        query_vector: list[float],
        model: str,
        limit: int = 8,
        talker: str = "",
    ) -> list[dict]:
        if not query_vector:
            return []
        limit = min(max(limit, 1), 30)
        talker_clause = ""
        params: list = [model, len(query_vector)]
        if talker:
            talker_clause = "AND k.talker = ?"
            params.append(talker)
        rows = await self._db.execute_fetchall(
            f"""SELECT k.*, e.vector
                FROM knowledge_embeddings e
                JOIN knowledge_chunks k ON k.id = e.chunk_id
                WHERE e.model = ? AND e.dimensions = ? {talker_clause}""",
            params,
        )
        scored = []
        for row in rows:
            item = dict(row)
            vector = unpack_vector(item.pop("vector"))
            item["score"] = dot_score(query_vector, vector)
            item["retrieval"] = "embedding"
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except json.JSONDecodeError:
                item["metadata"] = {}
            scored.append(item)
        scored.sort(key=lambda item: (float(item.get("score") or 0), int(item.get("end_time") or 0)), reverse=True)
        return scored[:limit]

    def _fts_query(self, query: str) -> str:
        import re

        terms = re.findall(r"[\w\u4e00-\u9fff]+", query)
        if not terms:
            return '""'
        return " OR ".join(f'"{term[:64].replace(chr(34), chr(34) + chr(34))}"' for term in terms[:8])

    def _search_terms(self, query: str) -> list[str]:
        import re

        normalized = query
        for phrase in ("聊了什么", "聊什么", "都聊了啥", "都聊什么", "最近", "大家", "关于", "有关"):
            normalized = normalized.replace(phrase, " ")
        normalized = re.sub(r"[和与及、，,。？?：:；;]+", " ", normalized)
        terms = re.findall(r"[\w\u4e00-\u9fff]+", normalized)
        stopwords = {"最近", "大家", "关于", "什么", "都", "聊了什么", "聊", "的", "和"}
        return [term for term in terms if term not in stopwords and len(term) >= 2][:8]

    async def search_knowledge(self, query: str, limit: int = 8, talker: str = "") -> list[dict]:
        clean = query.strip()
        if not clean:
            return []
        limit = min(max(limit, 1), 30)
        params: list = [self._fts_query(clean)]
        talker_clause = ""
        if talker:
            talker_clause = "AND k.talker = ?"
            params.append(talker)
        try:
            rows = await self._db.execute_fetchall(
                f"""SELECT k.*, bm25(knowledge_chunks_fts) AS score
                    FROM knowledge_chunks_fts
                    JOIN knowledge_chunks k ON k.id = knowledge_chunks_fts.rowid
                    WHERE knowledge_chunks_fts MATCH ? {talker_clause}
                    ORDER BY score ASC, k.end_time DESC
                    LIMIT ?""",
                params + [limit],
            )
        except Exception:
            rows = []
        if not rows:
            terms = self._search_terms(clean) or [clean]
            like_clauses = []
            like_params: list = []
            for term in terms:
                like_clauses.append("(title LIKE ? OR text LIKE ?)")
                like_params.extend([f"%{term}%", f"%{term}%"])
            talker_like_clause = ""
            if talker:
                talker_like_clause = "AND talker = ?"
                like_params.append(talker)
            rows = await self._db.execute_fetchall(
                f"""SELECT *, 0 AS score
                    FROM knowledge_chunks
                    WHERE ({' OR '.join(like_clauses)}) {talker_like_clause}
                    ORDER BY end_time DESC
                    LIMIT ?""",
                like_params + [limit],
            )
        results = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except json.JSONDecodeError:
                item["metadata"] = {}
            results.append(item)
        return results
