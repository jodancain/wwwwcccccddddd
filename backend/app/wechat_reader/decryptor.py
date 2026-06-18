"""Weixin 4.x database decryptor.

Extracts per-DB enc_keys from Weixin.exe memory and decrypts the SQLCipher 4
databases in db_storage/ into a plaintext mirror under
settings.decrypted_wx_dir.

The layout of the decrypted mirror preserves the subdirectory structure:
  <decrypted_wx_dir>/contact/contact.db
  <decrypted_wx_dir>/session/session.db
  <decrypted_wx_dir>/message/message_0.db
  ...
"""
import os
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config.settings import get_settings
from app.wechat_reader.wx4 import (
    auto_detect_db_dir,
    decrypt_database,
    verify_enc_key,
    find_all_keys,
)


def _sqlite_quick_check(path: Path) -> bool:
    try:
        conn = sqlite3.connect(str(path))
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
            return bool(result and result[0] == "ok")
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning(f"Decrypted DB failed SQLite check: {path.name}: {e}")
        return False


class WeChatDecryptor:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.output_dir: Path = self.settings.decrypted_wx_dir
        self._db_dir: Optional[str] = None
        self._keys: dict[str, dict] = {}
        self._last_init_failure_at = 0.0
        self._last_init_failure_msg = ""

    # ------------------------------------------------------------------
    # Discovery & key extraction
    # ------------------------------------------------------------------
    def _resolve_db_dir(self) -> str:
        override = getattr(self.settings, "WX_DB_DIR", "") or ""
        if override and os.path.isdir(override):
            return override
        detected = auto_detect_db_dir()
        if not detected:
            raise RuntimeError(
                "Could not locate Weixin 4.x db_storage directory. "
                "Set WX_DB_DIR in .env to the absolute path "
                r"(e.g. C:\Users\you\Documents\xwechat_files\wxid_xxx\db_storage)"
            )
        return detected

    def initialize(self) -> dict:
        """Locate db_storage and extract all per-DB enc_keys from Weixin.exe.

        Returns a dict compatible with the legacy callers:
          {"wx_dir": <db_storage path>, "keys": {rel: {enc_key,salt,size_mb}}}
        """
        self._db_dir = self._resolve_db_dir()
        logger.info(f"Weixin 4.x db_storage: {self._db_dir}")
        self._keys = self._keys_from_config() or self._load_cached_keys()
        if self._keys:
            logger.info(f"Loaded {len(self._keys)} cached/configured DB keys")
        else:
            now = time.time()
            if self._last_init_failure_at and now - self._last_init_failure_at < 30:
                raise RuntimeError(self._last_init_failure_msg)
            try:
                self._keys = find_all_keys(self._db_dir)
                self._save_cached_keys(self._keys)
                self._last_init_failure_at = 0.0
                self._last_init_failure_msg = ""
            except Exception as e:
                self._last_init_failure_at = now
                self._last_init_failure_msg = str(e)
                raise
        logger.info(f"Extracted {len(self._keys)} per-DB enc_keys")
        return {"wx_dir": self._db_dir, "keys": self._keys}

    def _key_cache_path(self) -> Path:
        return self.settings.data_path / "wx4_keys.json"

    def _collect_key_samples(self) -> list[tuple[str, str, int, str, bytes]]:
        if not self._db_dir:
            return []
        samples = []
        for rel in self._target_db_files():
            path = os.path.join(self._db_dir, rel)
            try:
                with open(path, "rb") as f:
                    page1 = f.read(4096)
                if len(page1) == 4096:
                    samples.append((rel, path, os.path.getsize(path), page1[:16].hex(), page1))
            except OSError:
                continue
        return samples

    def _keys_from_single_hex(self, key_hex: str, source: str) -> dict[str, dict]:
        key_hex = (key_hex or "").strip()
        if not key_hex:
            return {}
        try:
            key_material = bytes.fromhex(key_hex)
        except ValueError:
            logger.warning(f"Ignoring invalid {source}: not hex")
            return {}
        if len(key_material) != 32:
            logger.warning(f"Ignoring invalid {source}: expected 32 bytes")
            return {}
        keys: dict[str, dict] = {}
        for rel, _path, size, salt_hex, page1 in self._collect_key_samples():
            if verify_enc_key(key_material, page1):
                keys[rel] = {
                    "enc_key": key_hex,
                    "salt": salt_hex,
                    "size_mb": round(size / 1024 / 1024, 2),
                    "source": source,
                }
        if not keys:
            logger.warning(f"{source} did not verify against any target DB")
        return keys

    def _keys_from_config(self) -> dict[str, dict]:
        return self._keys_from_single_hex(getattr(self.settings, "WX_DB_KEY", ""), "WX_DB_KEY")

    def _load_cached_keys(self) -> dict[str, dict]:
        path = self._key_cache_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("db_dir") != self._db_dir:
                return {}
            cached = data.get("keys") or {}
            valid = {}
            for rel, _path, size, salt_hex, page1 in self._collect_key_samples():
                info = cached.get(rel) or cached.get(rel.replace(os.sep, "/"))
                if not info:
                    continue
                key_hex = info.get("enc_key", "")
                try:
                    key_material = bytes.fromhex(key_hex)
                except ValueError:
                    continue
                if verify_enc_key(key_material, page1):
                    valid[rel] = {
                        "enc_key": key_hex,
                        "salt": salt_hex,
                        "size_mb": round(size / 1024 / 1024, 2),
                        "source": "cache",
                    }
            return valid
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed reading key cache: {e}")
            return {}

    def _save_cached_keys(self, keys: dict[str, dict]) -> None:
        if not keys:
            return
        path = self._key_cache_path()
        try:
            path.write_text(
                json.dumps({"db_dir": self._db_dir, "keys": keys}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"Failed saving key cache: {e}")

    # ------------------------------------------------------------------
    # Full decrypt
    # ------------------------------------------------------------------
    # Only decrypt the DBs the rest of the app actually reads; skip large
    # FTS indexes and media blobs — those slow startup without adding features.
    _TARGET_RELS = (
        os.path.join("contact", "contact.db"),
        os.path.join("session", "session.db"),
        # message DBs are enumerated below (may be sharded message_0..N)
    )

    def _target_db_files(self) -> list[str]:
        rels = list(self._TARGET_RELS)
        if self._db_dir:
            msg_dir = os.path.join(self._db_dir, "message")
            if os.path.isdir(msg_dir):
                for name in sorted(os.listdir(msg_dir)):
                    if (
                        name.startswith("message_")
                        and name.endswith(".db")
                        and name[8:-3].isdigit()
                        and not name.endswith("-wal")
                        and not name.endswith("-shm")
                    ):
                        rels.append(os.path.join("message", name))
        return rels

    def decrypt_databases(self) -> str:
        """Decrypt contact.db, session.db and message_*.db to output_dir."""
        if not self._keys or not self._db_dir:
            self.initialize()

        out_root = self.output_dir
        out_root.mkdir(parents=True, exist_ok=True)

        ok = 0
        fail = 0
        for rel in self._target_db_files():
            # Normalise separators so JSON key lookup works on both
            key_info = self._keys.get(rel) or self._keys.get(rel.replace(os.sep, "/"))
            if not key_info:
                logger.debug(f"No key for {rel}, skipping")
                continue
            src = os.path.join(self._db_dir, rel)
            if not os.path.exists(src):
                continue
            dst_path = out_root / rel
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            # Decrypt to a sibling tmp file then atomically rename so readers
            # never see a half-written DB.
            tmp_path = dst_path.with_suffix(dst_path.suffix + ".tmp")
            try:
                if decrypt_database(src, str(tmp_path), bytes.fromhex(key_info["enc_key"])):
                    if not _sqlite_quick_check(tmp_path):
                        fail += 1
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                        continue
                    if dst_path.exists():
                        try:
                            dst_path.unlink()
                        except OSError:
                            pass
                    os.replace(str(tmp_path), str(dst_path))
                    ok += 1
                else:
                    fail += 1
                    if tmp_path.exists():
                        tmp_path.unlink()
            except Exception as e:  # noqa: BLE001
                fail += 1
                logger.warning(f"Decrypt {rel} failed: {e}")
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

        logger.info(f"Decrypted {ok} databases ({fail} failed)")
        return f"Decrypted {ok} databases"

    # ------------------------------------------------------------------
    # Helpers for parser / realtime listener
    # ------------------------------------------------------------------
    def decrypted_path(self, rel: str) -> Path:
        return self.output_dir / rel

    def source_db_dir(self) -> Optional[str]:
        return self._db_dir

    def get_decrypted_db_path(self, db_type: str = "message") -> list[Path]:
        """Legacy-ish helper: return decrypted .db files for a subdir."""
        sub = self.output_dir / db_type
        if not sub.exists():
            return []
        return sorted(p for p in sub.iterdir() if p.suffix == ".db")
