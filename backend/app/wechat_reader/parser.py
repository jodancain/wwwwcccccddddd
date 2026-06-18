"""Weixin 4.x message + contact reader.

Reads the decrypted plaintext mirror produced by :mod:`.decryptor`:

  <decrypted_wx_dir>/
      contact/contact.db          -> contacts + chatrooms
      session/session.db          -> SessionTable (sidebar list)
      message/message_0.db        -> per-chat Msg_<md5(username)> tables
      message/message_1.db        -> (sharded when large)
      ...

Message schema (Weixin 4.x is fundamentally different from 3.x):
  - Each chat has its own table named ``Msg_<md5hex_of_username>``.
  - ``real_sender_id`` is a rowid into a per-DB ``Name2Id`` table.
  - ``local_type`` is packed as ``(sub_type << 32) | type`` for some message
    types (type 49 variants notably); the low 32 bits hold the base type.
  - ``message_content`` is either raw UTF-8 text (for ``type=1`` plain text)
    or zstd-compressed XML for all other types.
"""
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import zstandard as zstd
from loguru import logger

from app.config.settings import get_settings
from app.wechat_reader.constants import get_type_name
from app.wechat_reader.models import Contact, WeChatMessage

_zstd_dctx = zstd.ZstdDecompressor()
_MESSAGE_DB_RE = re.compile(r"^message_\d+\.db$")
_READABLE_CACHE: dict[str, tuple[float, int, bool]] = {}


def _sqlite_readable(path: Path) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    cache_key = str(path)
    cached = _READABLE_CACHE.get(cache_key)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]
    try:
        conn = sqlite3.connect(str(path))
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
            ok = bool(result and result[0] == "ok")
            _READABLE_CACHE[cache_key] = (stat.st_mtime, stat.st_size, ok)
            return ok
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning(f"Skipping unreadable decrypted DB {path.name}: {e}")
        _READABLE_CACHE[cache_key] = (stat.st_mtime, stat.st_size, False)
        return False


def _maybe_decompress(raw: Optional[bytes]) -> str:
    """Decode message_content / compress_content bytes to a UTF-8 string.

    Content is either plain UTF-8 bytes (text messages) or zstd-compressed
    bytes (everything else). Returns "" for empty/None.
    """
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    # Quick path: zstd frame magic is 28 b5 2f fd
    if raw[:4] == b"\x28\xb5\x2f\xfd":
        try:
            # Content size may not be in frame header; cap expansion to 16 MB.
            return _zstd_dctx.decompress(raw, max_output_size=16 * 1024 * 1024).decode(
                "utf-8", errors="replace"
            )
        except Exception:
            try:
                with _zstd_dctx.stream_reader(raw) as rr:
                    return rr.read(16 * 1024 * 1024).decode("utf-8", errors="replace")
            except Exception:
                pass
    # Fallback: try utf-8 decode
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _split_type(local_type: int) -> tuple[int, int]:
    """Unpack ``(type, sub_type)`` from the Weixin 4.x packed local_type."""
    if local_type is None:
        return 1, 0
    base = int(local_type) & 0xFFFFFFFF
    sub = (int(local_type) >> 32) & 0xFFFFFFFF
    return base, sub


class WeChatMessageParser:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.decrypted_dir: Path = self.settings.decrypted_wx_dir
        self._self_wxid: str = ""
        self._contact_lookup_cache: Optional[dict[str, str]] = None

    # ------------------------------------------------------------------
    # Self-wxid detection (needed to set is_sender on outbound messages)
    # ------------------------------------------------------------------
    def _detect_self_wxid(self) -> str:
        if self._self_wxid:
            return self._self_wxid
        # Prefer parsing the source db_storage path: xwechat_files/<wxid>/db_storage
        override = getattr(self.settings, "WX_DB_DIR", "") or ""
        candidates = []
        if override:
            candidates.append(override)
        # Also read from decrypted path if we saved it; as a fallback do nothing.
        for path in candidates:
            parts = os.path.normpath(path).split(os.sep)
            for i, p in enumerate(parts):
                if p.startswith("wxid_") and i + 1 < len(parts) and parts[i + 1] == "db_storage":
                    wxid = p.rsplit("_", 1)[0] if re.match(r"^wxid_.+_[a-z0-9]+$", p) else p
                    self._self_wxid = wxid
                    return wxid
        return ""

    def set_self_wxid(self, wxid: str) -> None:
        """Called by the sync engine after initialize() so we know who 'me' is."""
        if wxid:
            self._self_wxid = wxid
            self._contact_lookup_cache = None

    # ------------------------------------------------------------------
    # Contacts (contact.db)
    # ------------------------------------------------------------------
    def _contact_db_path(self) -> Optional[Path]:
        p = self.decrypted_dir / "contact" / "contact.db"
        return p if p.exists() and _sqlite_readable(p) else None

    def get_contacts(self) -> list[Contact]:
        db_path = self._contact_db_path()
        if not db_path:
            logger.debug("No readable contact.db found in decrypted directory")
            return []
        contacts: list[Contact] = []
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT username, nick_name, remark, alias, local_type, verify_flag
                   FROM contact
                   WHERE username != '' AND delete_flag = 0"""
            ).fetchall()
            for r in rows:
                username = self._to_text(r["username"])
                if not username:
                    continue
                is_group = 1 if username.endswith("@chatroom") else 0
                contacts.append(
                    Contact(
                        username=username,
                        nickname=self._to_text(r["nick_name"]),
                        remark=self._to_text(r["remark"]),
                        alias=self._to_text(r["alias"]),
                        is_group=is_group,
                    )
                )
            conn.close()
            logger.info(f"Loaded {len(contacts)} contacts from contact.db")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to read contacts: {e}")
        return contacts

    def _build_contact_lookup(self) -> dict[str, str]:
        if self._contact_lookup_cache is not None:
            return self._contact_lookup_cache
        lookup: dict[str, str] = {}
        for c in self.get_contacts():
            name = c.remark or c.nickname or c.alias or c.username
            lookup[c.username] = name
            if c.alias:
                lookup.setdefault(c.alias, name)
        self._contact_lookup_cache = lookup
        return lookup

    # ------------------------------------------------------------------
    # Messages (message/message_N.db)
    # ------------------------------------------------------------------
    def _message_db_paths(self) -> list[Path]:
        msg_dir = self.decrypted_dir / "message"
        if not msg_dir.exists():
            return []
        return sorted(
            p
            for p in msg_dir.iterdir()
            if _MESSAGE_DB_RE.match(p.name) and _sqlite_readable(p)
        )

    def get_messages(self, since_timestamp: int = 0) -> list[WeChatMessage]:
        db_paths = self._message_db_paths()
        if not db_paths:
            logger.debug("No readable message_*.db files found in decrypted directory")
            return []

        self._detect_self_wxid()
        contact_lookup = self._build_contact_lookup()

        messages: list[WeChatMessage] = []
        for db_path in db_paths:
            try:
                messages.extend(
                    self._read_messages_from_db(db_path, since_timestamp, contact_lookup)
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to read {db_path.name}: {e}")
        messages.sort(key=lambda m: m.create_time)
        logger.info(
            f"Loaded {len(messages)} messages since timestamp {since_timestamp}"
        )
        return messages

    def _read_messages_from_db(
        self,
        db_path: Path,
        since_timestamp: int,
        contact_lookup: dict[str, str],
    ) -> list[WeChatMessage]:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Per-DB Name2Id: rowid -> user_name. Build a full mapping once.
        name2id: dict[int, str] = {}
        try:
            for r in conn.execute("SELECT rowid, user_name, is_session FROM Name2Id"):
                name2id[int(r["rowid"])] = self._to_text(r["user_name"])
        except sqlite3.OperationalError:
            conn.close()
            return []

        # Which Name2Id rows are sessions (chats) → corresponding Msg_<md5> table?
        chat_usernames = [
            un for rid, un in name2id.items() if un and self._is_chat_username(un)
        ]

        msg_tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
            )
        }

        messages: list[WeChatMessage] = []
        for talker in chat_usernames:
            table_name = f"Msg_{hashlib.md5(talker.encode('utf-8')).hexdigest()}"
            if table_name not in msg_tables:
                continue
            is_group = 1 if talker.endswith("@chatroom") else 0
            try:
                rows = conn.execute(
                    f"""SELECT local_id, server_id, local_type, real_sender_id,
                               create_time, message_content, compress_content
                        FROM {table_name}
                        WHERE create_time > ?
                        ORDER BY create_time ASC""",
                    (since_timestamp,),
                ).fetchall()
            except sqlite3.OperationalError:
                continue

            for row in rows:
                msg = self._build_message(
                    row, talker, is_group, name2id, contact_lookup
                )
                if msg is not None:
                    messages.append(msg)
        conn.close()
        return messages

    @staticmethod
    def _is_chat_username(username: str) -> bool:
        if not username:
            return False
        if username.startswith("gh_"):
            return True  # official account
        if username.endswith("@chatroom") or username.endswith("@openim"):
            return True
        if username.startswith("wxid_") or username.startswith("weixin"):
            return True
        # Everyday alias accounts (QQ-style) also show up as sessions
        return bool(re.match(r"^[A-Za-z][A-Za-z0-9_\-]{4,}$", username))

    def _build_message(
        self,
        row: sqlite3.Row,
        talker: str,
        is_group: int,
        name2id: dict[int, str],
        contact_lookup: dict[str, str],
    ) -> Optional[WeChatMessage]:
        base_type, sub_type = _split_type(row["local_type"])
        create_time = int(row["create_time"] or 0)
        if create_time <= 0:
            return None

        raw_content = _maybe_decompress(row["message_content"])
        compress_content = _maybe_decompress(row["compress_content"])

        sender_wxid = name2id.get(int(row["real_sender_id"] or 0), "")

        # Group messages store "<sender_wxid>:\n<actual_content>"; strip prefix.
        text_content = raw_content
        if is_group:
            m = re.match(r"^([\w\-@.]+?):\n([\s\S]*)$", raw_content)
            if m:
                if not sender_wxid:
                    sender_wxid = m.group(1)
                text_content = m.group(2)

        is_sender = 1 if sender_wxid and sender_wxid == self._self_wxid else 0
        if is_group and is_sender:
            sender_display = "我"
        elif sender_wxid:
            sender_display = contact_lookup.get(sender_wxid, sender_wxid)
        else:
            sender_display = ""

        display_content = ""
        # Type-specific rendering
        if base_type == 1:
            content = text_content.strip() or raw_content
        elif base_type == 3:  # image
            content = "[图片]"
            meta = self._parse_image_xml(text_content)
            if meta:
                display_content = json.dumps(meta, ensure_ascii=False)
        elif base_type == 34:  # voice
            content = "[语音]"
        elif base_type == 42:  # contact card
            card_name = self._extract_attr(text_content, "nickname")
            content = f"[名片] {card_name}" if card_name else "[名片]"
        elif base_type == 43:  # video
            content = "[视频]"
        elif base_type == 47:  # emoji
            content = "[表情]"
            meta = self._parse_emoji_xml(text_content)
            if meta:
                display_content = json.dumps(meta, ensure_ascii=False)
        elif base_type == 48:
            content = "[位置]"
        elif base_type == 49:  # link / file / quoted / etc.
            meta = self._parse_link_xml(text_content)
            if meta:
                display_content = json.dumps(meta, ensure_ascii=False)
                title = meta.get("title", "")
                content = f"[链接] {title}" if title else "[链接]"
            else:
                content = "[链接]"
        elif base_type == 50:
            content = "[通话]"
        elif base_type in (10000,):
            content = self._extract_sysmsg_text(text_content) or "[系统消息]"
        elif base_type == 10002 or "<revokemsg" in text_content.lower():
            content = "[撤回消息]"
        else:
            content = (text_content.strip() if text_content else "") or f"[{get_type_name(base_type)}]"

        content = self._sanitize_text(content)
        display_content = self._sanitize_text(display_content)
        sender_display = self._sanitize_text(sender_display)
        create_date = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d")

        return WeChatMessage(
            local_id=int(row["local_id"] or 0),
            msg_svr_id=str(row["server_id"] or ""),
            talker=talker,
            sender=sender_display,
            type=base_type,
            type_name=get_type_name(base_type),
            is_sender=is_sender,
            content=content,
            display_content=display_content,
            create_time=create_time,
            create_date=create_date,
            is_group=is_group,
        )

    # ------------------------------------------------------------------
    # XML helpers (mostly reused from v3 parser)
    # ------------------------------------------------------------------
    def _parse_link_xml(self, xml: str) -> Optional[dict]:
        if not xml:
            return None
        meta: dict[str, str] = {}
        for tag, key in [
            ("title", "title"),
            ("des", "des"),
            ("url", "url"),
            ("thumburl", "thumburl"),
            ("sourcedisplayname", "source"),
            ("type", "apptype"),
        ]:
            m = re.search(rf"<{tag}><!\[CDATA\[(.*?)\]\]></{tag}>", xml, re.DOTALL)
            if not m:
                m = re.search(rf"<{tag}>([^<\x00]*?)</{tag}>", xml, re.DOTALL)
            if m:
                val = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", m.group(1).strip())
                if val:
                    meta[key] = val[:500]
        if "url" in meta:
            meta["url"] = meta["url"].replace("&amp;", "&")
        return meta if meta.get("title") or meta.get("url") else None

    def _parse_emoji_xml(self, xml: str) -> Optional[dict]:
        if not xml or "<emoji" not in xml:
            return None
        meta: dict[str, str] = {}
        for attr, key in [("cdnurl", "cdnurl"), ("md5", "md5"), ("type", "emoji_type"), ("len", "len")]:
            m = re.search(rf'{attr}="([^"]*)"', xml)
            if m:
                meta[key] = m.group(1).replace("&amp;", "&")
        return meta if meta.get("cdnurl") or meta.get("md5") else None

    def _parse_image_xml(self, xml: str) -> Optional[dict]:
        if not xml or "<img" not in xml:
            return None
        meta: dict[str, str] = {}
        for attr in ("length", "hdlength", "cdnbigimgurl", "cdnmidimgurl", "cdnthumburl"):
            m = re.search(rf'{attr}="([^"]*)"', xml)
            if m and m.group(1):
                meta[attr] = m.group(1)
        return meta or None

    def _extract_attr(self, xml: str, attr: str) -> str:
        if not xml:
            return ""
        m = re.search(rf'{attr}="([^"]*)"', xml)
        return m.group(1) if m else ""

    def _extract_sysmsg_text(self, xml: str) -> str:
        if not xml:
            return ""
        m = re.search(r"<content>(.*?)</content>", xml, re.DOTALL)
        return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""

    # ------------------------------------------------------------------
    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Strip lone UTF-16 surrogates.

        Weixin occasionally stores text whose trailing multibyte sequence was
        truncated mid-codepoint; Python 3's 'utf-8 errors=surrogateescape' then
        smuggles the raw bytes back out as 0xDCxx low surrogates. These are not
        valid JSON codepoints, so filter them.
        """
        if not text:
            return text
        return "".join(ch for ch in text if not (0xD800 <= ord(ch) <= 0xDFFF))

    def _to_text(self, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            for enc in ("utf-8", "gb18030", "utf-16-le"):
                try:
                    return value.decode(enc)
                except Exception:
                    continue
            return value.decode("utf-8", errors="ignore")
        return str(value)

    # ------------------------------------------------------------------
    # Session list (for sidebar + real-time polling)
    # ------------------------------------------------------------------
    def get_sessions(self) -> list[dict]:
        """Return SessionTable rows sorted by last_timestamp desc."""
        session_db = self.decrypted_dir / "session" / "session.db"
        if not session_db.exists():
            return []
        out: list[dict] = []
        try:
            conn = sqlite3.connect(str(session_db))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT username, unread_count, summary, last_timestamp,
                          last_msg_type, last_msg_sender, last_sender_display_name,
                          last_msg_locald_id
                   FROM SessionTable
                   WHERE last_timestamp > 0
                   ORDER BY last_timestamp DESC"""
            ).fetchall()
            for r in rows:
                summary_raw = r["summary"]
                summary = (
                    _maybe_decompress(summary_raw)
                    if isinstance(summary_raw, (bytes, bytearray))
                    else (summary_raw or "")
                )
                out.append(
                    {
                        "username": self._to_text(r["username"]),
                        "unread": int(r["unread_count"] or 0),
                        "summary": self._sanitize_text(summary),
                        "last_timestamp": int(r["last_timestamp"] or 0),
                        "last_msg_type": int(r["last_msg_type"] or 0),
                        "last_msg_sender": self._to_text(r["last_msg_sender"]),
                        "last_sender_display": self._to_text(
                            r["last_sender_display_name"]
                        ),
                        "last_msg_local_id": int(r["last_msg_locald_id"] or 0),
                    }
                )
            conn.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed reading session.db: {e}")
        return out
