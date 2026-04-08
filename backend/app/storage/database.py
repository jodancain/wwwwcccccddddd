import json
import uuid
from datetime import datetime
from typing import Optional

import aiosqlite
from loguru import logger

from app.storage.migrations import SCHEMA_SQL


class AppDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()
        logger.info(f"Database connected: {self.db_path}")

    async def close(self):
        if self._db:
            await self._db.close()
            logger.info("Database closed")

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
            search_clause = "HAVING c.nickname LIKE ? OR c.remark LIKE ?"
            params = [f"%{search}%", f"%{search}%"]
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

    async def get_all_recent_messages(self, hours: int = 24, limit: int = 5000) -> list[dict]:
        """Load recent messages across ALL conversations within the last N hours."""
        import time
        since_ts = int(time.time()) - hours * 3600
        rows = await self._db.execute_fetchall(
            """SELECT m.talker, m.sender, m.type, m.type_name, m.is_sender,
                      m.content, m.create_time, m.create_date, m.is_group,
                      c.nickname, c.remark
               FROM messages m
               LEFT JOIN contacts c ON m.talker = c.username
               WHERE m.create_time > ?
               ORDER BY m.create_time ASC
               LIMIT ?""",
            (since_ts, limit),
        )
        return [dict(r) for r in rows]

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
