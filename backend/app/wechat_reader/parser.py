import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.wechat_reader.constants import get_type_name
from app.wechat_reader.models import Contact, WeChatMessage
from app.config.settings import get_settings


class WeChatMessageParser:
    def __init__(self):
        self.settings = get_settings()
        self.decrypted_dir = self.settings.decrypted_wx_dir

    def get_contacts(self) -> list[Contact]:
        contacts = []
        db_paths = list(self.decrypted_dir.glob("*MicroMsg*.db"))
        if not db_paths:
            logger.warning("No MicroMsg database found in decrypted directory")
            return contacts

        for db_path in db_paths:
            try:
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                tables = [r[0] for r in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()]

                if "Contact" in tables:
                    rows = cursor.execute(
                        """SELECT UserName, Alias, NickName, Remark, Type, VerifyFlag
                           FROM Contact
                           WHERE VerifyFlag = 0 AND Type != 0"""
                    ).fetchall()

                    for row in rows:
                        username = self._to_text(row["UserName"])
                        is_group = 1 if username.endswith("@chatroom") else 0
                        contacts.append(Contact(
                            username=username,
                            nickname=self._to_text(row["NickName"]),
                            remark=self._to_text(row["Remark"]),
                            alias=self._to_text(row["Alias"]),
                            is_group=is_group,
                        ))

                conn.close()
                logger.info(f"Loaded {len(contacts)} contacts from {db_path.name}")
            except Exception as e:
                logger.error(f"Failed to read contacts from {db_path}: {e}")

        return contacts

    def get_messages(self, since_timestamp: int = 0) -> list[WeChatMessage]:
        messages = []
        db_paths = sorted(self.decrypted_dir.glob("*MSG*.db"))
        if not db_paths:
            logger.warning("No MSG databases found in decrypted directory")
            return messages

        for db_path in db_paths:
            try:
                msgs = self._read_messages_from_db(db_path, since_timestamp)
                messages.extend(msgs)
            except Exception as e:
                logger.error(f"Failed to read messages from {db_path}: {e}")

        messages.sort(key=lambda m: m.create_time)
        logger.info(f"Loaded {len(messages)} messages since timestamp {since_timestamp}")
        return messages

    def _read_messages_from_db(self, db_path: Path,
                                since_timestamp: int) -> list[WeChatMessage]:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        tables = [r[0] for r in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]

        if "MSG" not in tables:
            conn.close()
            return []

        # Build contact name lookup for resolving wxid -> display name
        contact_names = self._build_contact_lookup()

        # Include CompressContent and BytesExtra
        rows = cursor.execute(
            """SELECT localId, MsgSvrID, TalkerId, Type, SubType, IsSender,
                      CreateTime, StrTalker, StrContent, DisplayContent,
                      CompressContent, BytesExtra
               FROM MSG
               WHERE CreateTime > ?
               ORDER BY CreateTime ASC""",
            (since_timestamp,),
        ).fetchall()

        messages = []
        for row in rows:
            talker = self._to_text(row["StrTalker"])
            content = self._to_text(row["StrContent"])
            display_content = self._to_text(row["DisplayContent"])
            msg_type = row["Type"] or 1
            sub_type = row["SubType"] or 0
            is_group = 1 if talker.endswith("@chatroom") else 0
            create_time = row["CreateTime"] or 0
            compress_content = row["CompressContent"]
            bytes_extra = row["BytesExtra"]

            sender = ""
            raw_content = content

            # Extract sender for group messages
            if is_group and not (row["IsSender"] or 0):
                # Try BytesExtra first (most reliable)
                sender_id = self._extract_sender_from_bytes_extra(bytes_extra)
                if sender_id:
                    sender = contact_names.get(sender_id, sender_id)
                # Fallback: try content prefix format "wxid:\ncontent"
                if not sender and content:
                    extracted_id, content = self._extract_group_sender(content)
                    raw_content = content
                    if extracted_id:
                        sender = contact_names.get(extracted_id, extracted_id)
            elif is_group and content:
                _, content = self._extract_group_sender(content)
                raw_content = content

            # For link messages, parse CompressContent XML to extract metadata
            if msg_type == 49 and compress_content and len(compress_content) > 50:
                link_meta = self._parse_link_xml(compress_content)
                if link_meta:
                    display_content = json.dumps(link_meta, ensure_ascii=False)
                    title = link_meta.get("title", "")
                    content = f"[链接] {title}" if title else "[链接]"
                else:
                    content = self._build_message_preview(msg_type, raw_content, display_content)
            # For emoji messages, extract emoji URL from XML
            elif msg_type == 47:
                emoji_meta = self._parse_emoji_xml(raw_content)
                if emoji_meta:
                    display_content = json.dumps(emoji_meta, ensure_ascii=False)
                    content = "[表情]"
                else:
                    content = self._build_message_preview(msg_type, raw_content, display_content)
            # For image messages, extract image metadata
            elif msg_type == 3:
                img_meta = self._parse_image_xml(raw_content)
                if img_meta:
                    display_content = json.dumps(img_meta, ensure_ascii=False)
                content = "[图片]"
            else:
                content = self._build_message_preview(msg_type, content, display_content)

            # Sanitize all text to be JSON-safe (remove surrogates)
            content = self._sanitize_text(content)
            display_content = self._sanitize_text(display_content)
            sender = self._sanitize_text(sender)

            create_date = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d") if create_time else ""

            messages.append(WeChatMessage(
                local_id=row["localId"] or 0,
                msg_svr_id=str(row["MsgSvrID"] or ""),
                talker=talker,
                sender=sender,
                type=msg_type,
                type_name=get_type_name(msg_type),
                is_sender=row["IsSender"] or 0,
                content=content,
                display_content=display_content,
                create_time=create_time,
                create_date=create_date,
                is_group=is_group,
            ))

        conn.close()
        return messages

    def _parse_link_xml(self, compress_content: bytes) -> dict | None:
        """Parse link message XML from CompressContent.

        CompressContent for type 49 has a 2-byte header followed by raw XML.
        The XML may contain binary artifacts, so we use robust regex matching.
        """
        try:
            xml_start = compress_content.find(b"<")
            if xml_start < 0:
                return None
            xml = compress_content[xml_start:].decode("utf-8", errors="ignore")

            meta = {}
            # Extract fields with robust patterns that handle garbled closing tags
            for tag, key in [
                ("title", "title"),
                ("des", "des"),
                ("url", "url"),
                ("thumburl", "thumburl"),
                ("sourcedisplayname", "source"),
                ("type", "apptype"),
            ]:
                # Try CDATA pattern first, then plain tag
                # Use [^<]* instead of .* to avoid matching across garbled tags
                m = re.search(
                    rf"<{tag}><!\[CDATA\[(.*?)\]\]></{tag}>",
                    xml, re.DOTALL,
                )
                if not m:
                    # Match content up to next tag or garbled bytes
                    m = re.search(
                        rf"<{tag}>([^<\x00]*?)(?:</{tag}>|</>|\x00)",
                        xml, re.DOTALL,
                    )
                if m:
                    val = m.group(1).strip()
                    # Clean up binary artifacts and non-printable chars
                    val = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", val)
                    # Remove replacement chars
                    val = val.replace("\ufffd", "")
                    if val:
                        meta[key] = val[:500]

            # Fix URL encoding
            if "url" in meta:
                meta["url"] = meta["url"].replace("&amp;", "&")

            return meta if meta.get("title") or meta.get("url") else None
        except Exception:
            return None

    def _parse_emoji_xml(self, content: str) -> dict | None:
        """Extract emoji metadata from StrContent XML."""
        if not content or "<emoji" not in content:
            return None
        try:
            meta = {}
            m = re.search(r'cdnurl="([^"]*)"', content)
            if m:
                meta["cdnurl"] = m.group(1).replace("&amp;", "&")
            m = re.search(r'md5="([^"]*)"', content)
            if m:
                meta["md5"] = m.group(1)
            m = re.search(r'type="(\d+)"', content)
            if m:
                meta["emoji_type"] = m.group(1)
            m = re.search(r'len="(\d+)"', content)
            if m:
                meta["len"] = int(m.group(1))
            return meta if meta.get("cdnurl") or meta.get("md5") else None
        except Exception:
            return None

    def _parse_image_xml(self, content: str) -> dict | None:
        """Extract image metadata from StrContent XML."""
        if not content or "<img" not in content:
            return None
        try:
            meta = {}
            m = re.search(r'length="(\d+)"', content)
            if m:
                meta["length"] = int(m.group(1))
            m = re.search(r'hdlength="(\d+)"', content)
            if m:
                meta["hdlength"] = int(m.group(1))
            m = re.search(r'cdnbigimgurl="([^"]*)"', content)
            if m and m.group(1):
                meta["cdnbigimgurl"] = m.group(1)
            m = re.search(r'cdnmidimgurl="([^"]*)"', content)
            if m and m.group(1):
                meta["cdnmidimgurl"] = m.group(1)
            m = re.search(r'cdnthumburl="([^"]*)"', content)
            if m and m.group(1):
                meta["cdnthumburl"] = m.group(1)
            return meta if meta else None
        except Exception:
            return None

    def _build_contact_lookup(self) -> dict[str, str]:
        """Build a username -> display_name mapping from all contacts."""
        contacts = self.get_contacts()
        lookup = {}
        for c in contacts:
            # Use remark first, then nickname, then alias
            name = c.remark or c.nickname or c.alias or c.username
            lookup[c.username] = name
            if c.alias:
                lookup[c.alias] = name
        return lookup

    @staticmethod
    def _extract_sender_from_bytes_extra(bytes_extra: bytes | None) -> str:
        """Extract sender wxid/username from BytesExtra protobuf field."""
        if not bytes_extra or len(bytes_extra) < 10:
            return ""
        try:
            # Pattern: 0x08 0x01 0x12 <length_byte> <sender_id>
            idx = bytes_extra.find(b"\x08\x01\x12")
            if idx < 0:
                return ""
            after = bytes_extra[idx + 3:]
            if not after:
                return ""
            length = after[0]
            if length <= 0 or length > 64:
                return ""
            sender = after[1:1 + length].decode("utf-8", errors="ignore")
            # Validate: should look like a username
            if sender and re.match(r"^[\w\-@.]+$", sender):
                return sender
        except Exception:
            pass
        return ""

    def _extract_group_sender(self, content: str) -> tuple[str, str]:
        match = re.match(r"^([\w\-@.]+?):\n([\s\S]*)$", content)
        if match:
            return match.group(1), match.group(2)
        return "", content

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Remove surrogates and control chars to produce JSON-safe strings."""
        if not text:
            return text
        # Encode then decode to strip surrogates
        return text.encode("utf-8", errors="replace").decode("utf-8", errors="ignore")

    def _build_message_preview(self, msg_type: int, content: str, display_content: str) -> str:
        text = (content or "").strip()
        if msg_type == 1 and text and not self._looks_like_xml(text):
            return text

        if msg_type == 3:
            return "[图片]"
        if msg_type == 34:
            return "[语音]"
        if msg_type == 42:
            return self._parse_contact_card(text) or "[名片]"
        if msg_type == 43:
            return "[视频]"
        if msg_type == 47:
            return "[表情]"
        if msg_type == 48:
            return "[位置]"
        if msg_type == 49:
            title = self._extract_xml_title(text)
            return f"[链接] {title}" if title else "[链接]"
        if msg_type == 50:
            return "[通话]"
        if msg_type == 10002 or "<revokemsg" in text.lower():
            return "[撤回消息]"
        if msg_type == 10000:
            return display_content or "[系统消息]"

        if self._looks_like_xml(text):
            return f"[{get_type_name(msg_type)}]"

        return text or display_content or f"[{get_type_name(msg_type)}]"

    def _parse_contact_card(self, content: str) -> str | None:
        """Extract name from contact card XML."""
        if not content:
            return None
        m = re.search(r'nickname="([^"]*)"', content)
        if m:
            return f"[名片] {m.group(1)}"
        return None

    def _extract_xml_title(self, raw_text: str) -> str:
        if not raw_text:
            return ""
        match = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", raw_text)
        if not match:
            return ""
        return (match.group(1) or match.group(2) or "").strip()

    def _looks_like_xml(self, text: str) -> bool:
        t = (text or "").lstrip()
        return t.startswith("<") and ">" in t

    def _to_text(self, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            if b"\x00" in value:
                for enc in ("utf-16-le", "utf-8", "gb18030"):
                    try:
                        return value.decode(enc)
                    except Exception:
                        continue
            for enc in ("utf-8", "gb18030", "utf-16-le"):
                try:
                    return value.decode(enc)
                except Exception:
                    continue
            return value.decode("utf-8", errors="ignore")
        return str(value)
