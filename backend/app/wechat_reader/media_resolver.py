import re
import time
from pathlib import Path
from typing import Optional

from app.config.settings import get_settings


class WeChatMediaResolver:
    HASH_INDEX_TIME_BUDGET_SECONDS = 1.0

    def __init__(self):
        self.settings = get_settings()
        self.msg_db = self.settings.decrypted_wx_dir / "MSG.db"
        self._msg_attach_dir: Optional[Path] = None
        self._hash_index: dict[str, Path] | None = None
        self._msg_index_ready = False

    def _detect_msg_attach_dir(self) -> Optional[Path]:
        wx_dir = None
        try:
            import wdecipher
            infos = wdecipher.get_wx_infos()
            if infos:
                info = infos[0]
                wx_dir = info.get("wx_dir") or info.get("filePath")
        except Exception:
            wx_dir = None

        if wx_dir:
            p = Path(wx_dir) / "FileStorage" / "MsgAttach"
            if p.exists():
                return p

        base = Path.home() / "Documents" / "WeChat Files"
        if base.exists():
            for child in base.iterdir():
                p = child / "FileStorage" / "MsgAttach"
                if p.exists():
                    return p
        return None

    def _extract_image_path_from_bytes_extra(self, bytes_extra: object) -> list[Path]:
        if not bytes_extra:
            return []
        if isinstance(bytes_extra, bytes):
            text = bytes_extra.decode("utf-8", errors="ignore")
        else:
            text = str(bytes_extra)

        rel_paths = re.findall(
            r"(wxid_[^\\]+\\FileStorage\\MsgAttach\\[^\\]+\\(?:Image|Thumb)\\[^\\]+\\[^\\]+?\.dat(?:[0-9a-f]{32})?)",
            text,
            flags=re.IGNORECASE,
        )
        out: list[Path] = []
        for rel in rel_paths:
            clean = rel
            if ".dat" in clean.lower():
                idx = clean.lower().find(".dat")
                clean = clean[: idx + 4]
            p = Path.home() / "Documents" / "WeChat Files" / Path(clean)
            out.append(p)
        return out

    def _extract_hash_keys_from_xml(self, content: str) -> list[str]:
        keys = re.findall(r'(?:md5|originsourcemd5)="([0-9a-fA-F]{32})"', content or "")
        return [k.lower() for k in keys]

    def _build_hash_index(self) -> dict[str, Path]:
        index: dict[str, Path] = {}
        if self._msg_attach_dir is None:
            self._msg_attach_dir = self._detect_msg_attach_dir()
        if not self._msg_attach_dir or not self._msg_attach_dir.exists():
            return index

        deadline = time.monotonic() + self.HASH_INDEX_TIME_BUDGET_SECONDS
        for file in self._msg_attach_dir.rglob("*"):
            if time.monotonic() >= deadline:
                break
            if not file.is_file():
                continue
            low = file.name.lower()
            if ".dat" not in low:
                continue
            if "\\image\\" not in str(file).lower() and "\\thumb\\" not in str(file).lower():
                continue

            hashes = re.findall(r"[0-9a-f]{32}", low)
            for h in hashes:
                old = index.get(h)
                if not old or ("\\thumb\\" in str(old).lower() and "\\image\\" in str(file).lower()):
                    index[h] = file
        return index

    def _resolve_dat_path(self, local_id: int) -> Optional[Path]:
        import sqlite3

        if not self.msg_db.exists():
            return None

        conn = None
        try:
            conn = sqlite3.connect(str(self.msg_db), timeout=0.5)
            if not self._msg_index_ready:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_local_id ON MSG(localId)")
                conn.commit()
                self._msg_index_ready = True
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT Type, StrContent, BytesExtra FROM MSG WHERE localId = ? LIMIT 1",
                (local_id,),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            if conn:
                conn.close()
        if not row or row["Type"] != 3:
            return None

        for p in self._extract_image_path_from_bytes_extra(row["BytesExtra"]):
            if p.exists():
                return p

        content = row["StrContent"]
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="ignore")
        content = content or ""
        keys = self._extract_hash_keys_from_xml(content)
        if not keys:
            return None
        return None

    @staticmethod
    def _decode_dat_to_image(raw: bytes) -> tuple[bytes, str] | tuple[None, None]:
        signatures: list[tuple[bytes, str]] = [
            (bytes.fromhex("FFD8FF"), "image/jpeg"),
            (bytes.fromhex("89504E47"), "image/png"),
            (b"GIF8", "image/gif"),
            (b"BM", "image/bmp"),
            (b"RIFF", "image/webp"),
        ]
        if not raw:
            return None, None

        for sig, mime in signatures:
            key = raw[0] ^ sig[0]
            if all((raw[i] ^ key) == sig[i] for i in range(min(len(sig), len(raw)))):
                decoded = bytes(b ^ key for b in raw)
                return decoded, mime
        return None, None

    def load_image_bytes(self, local_id: int) -> tuple[bytes, str] | tuple[None, None]:
        p = self._resolve_dat_path(local_id)
        if not p or not p.exists():
            return None, None

        raw = p.read_bytes()
        decoded, mime = self._decode_dat_to_image(raw)
        if decoded:
            return decoded, mime

        if raw.startswith(bytes.fromhex("FFD8FF")):
            return raw, "image/jpeg"
        if raw.startswith(bytes.fromhex("89504E47")):
            return raw, "image/png"
        return None, None
