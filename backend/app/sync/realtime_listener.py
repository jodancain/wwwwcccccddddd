"""Real-time Weixin 4.x message listener (WAL-mtime polling).

Weixin 4.x writes all incoming messages into session.db (the sidebar list) and
into the sharded message_*.db files. Their SQLite -wal sidecar files are
rewritten every time WeChat commits a new message, so watching the WAL mtime
is the fastest filesystem-level change detector (~100ms end-to-end delay).

This replaces the v3-era wxauto UI-polling listener:
  - No UI Automation → no window class / hwnd coupling.
  - Works as long as we have the per-DB enc_keys in memory (see
    :class:`WeChatDecryptor`).
  - Fires a light decrypt of session.db only; the main sync engine then runs
    the full re-decrypt + parse cycle.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
import time
from typing import Optional

from loguru import logger

from app.config.settings import get_settings
from app.wechat_reader.wx4 import decrypt_db_to_tempfile


class RealtimeListener:
    # Poll the WAL every 500 ms; that is fast enough to feel instant and slow
    # enough to not cost any measurable CPU on an idle machine.
    _POLL_INTERVAL = 0.5

    def __init__(self) -> None:
        self.settings = get_settings()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._new_messages_queue: list[dict] = []
        self._prev_sessions: dict[str, int] = {}  # username -> last_timestamp
        self._prev_wal_mtime: float = 0.0
        self._decryptor = None  # set lazily to avoid circular imports
        self.status = "idle"
        self.connected = False
        self.error = ""

    # ------------------------------------------------------------------
    def _get_decryptor(self):
        if self._decryptor is None:
            from app.wechat_reader.decryptor import WeChatDecryptor

            self._decryptor = WeChatDecryptor()
        return self._decryptor

    def _session_paths(self) -> tuple[Optional[str], Optional[str]]:
        """Return (session.db, session.db-wal) from the *source* db_storage."""
        dec = self._get_decryptor()
        if not dec.source_db_dir():
            try:
                dec.initialize()
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                return None, None
        root = dec.source_db_dir()
        if not root:
            return None, None
        db = os.path.join(root, "session", "session.db")
        wal = db + "-wal"
        return (db if os.path.exists(db) else None, wal if os.path.exists(wal) else None)

    def _session_enc_key(self) -> Optional[bytes]:
        dec = self._get_decryptor()
        if not dec._keys:
            try:
                dec.initialize()
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                return None
        rel_variants = (
            os.path.join("session", "session.db"),
            "session/session.db",
            "session\\session.db",
        )
        for rel in rel_variants:
            info = dec._keys.get(rel)
            if info:
                return bytes.fromhex(info["enc_key"])
        return None

    def _snapshot_sessions(self, db_path: str, enc_key: bytes) -> dict[str, int]:
        """Decrypt session.db in-place to a tmp file and read SessionTable.

        Returns {username: last_timestamp} — small payload, fast diff.
        """
        tmp_path = decrypt_db_to_tempfile(db_path, enc_key, suffix=".session.tmp")
        try:
            conn = sqlite3.connect(tmp_path)
            rows = conn.execute(
                "SELECT username, last_timestamp FROM SessionTable WHERE last_timestamp > 0"
            ).fetchall()
            conn.close()
            return {r[0]: int(r[1] or 0) for r in rows}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    def _poll_loop(self) -> None:
        logger.info("Realtime listener polling started (session.db WAL)")
        self.status = "listening"
        while self._running:
            try:
                db_path, wal_path = self._session_paths()
                if not db_path:
                    self.connected = False
                    time.sleep(2.0)
                    continue

                enc_key = self._session_enc_key()
                if not enc_key:
                    self.connected = False
                    time.sleep(2.0)
                    continue
                self.connected = True
                self.error = ""

                # Cheap gate: only decrypt when the WAL has moved.
                wal_mtime = 0.0
                if wal_path and os.path.exists(wal_path):
                    try:
                        wal_mtime = os.path.getmtime(wal_path)
                    except OSError:
                        wal_mtime = 0.0
                else:
                    # No WAL → fall back to the DB mtime
                    try:
                        wal_mtime = os.path.getmtime(db_path)
                    except OSError:
                        wal_mtime = 0.0

                if wal_mtime and wal_mtime == self._prev_wal_mtime:
                    time.sleep(self._POLL_INTERVAL)
                    continue
                self._prev_wal_mtime = wal_mtime

                try:
                    curr = self._snapshot_sessions(db_path, enc_key)
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"snapshot_sessions failed: {e}")
                    time.sleep(self._POLL_INTERVAL * 2)
                    continue

                if not self._prev_sessions:
                    self._prev_sessions = curr
                    time.sleep(self._POLL_INTERVAL)
                    continue

                changed = [
                    un
                    for un, ts in curr.items()
                    if ts > self._prev_sessions.get(un, 0)
                ]
                if changed:
                    now = time.time()
                    with self._lock:
                        for un in changed:
                            self._new_messages_queue.append(
                                {"chat_name": un, "timestamp": now}
                            )
                    logger.info(f"session.db change: {changed[:5]} (+{max(0, len(changed)-5)})")
                self._prev_sessions = curr
            except Exception as e:  # noqa: BLE001
                logger.warning(f"realtime poll error: {e}")
                self.error = str(e)
                time.sleep(2.0)
            time.sleep(self._POLL_INTERVAL)
        self.status = "stopped"
        logger.info("Realtime listener stopped")

    # ------------------------------------------------------------------
    def get_pending_changes(self) -> list[dict]:
        with self._lock:
            out = self._new_messages_queue.copy()
            self._new_messages_queue.clear()
        return out

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        await asyncio.sleep(0)  # yield

    def stop(self) -> None:
        self._running = False
        self.status = "stopped"

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "connected": self.connected,
            "status": self.status,
            "error": self.error,
        }


realtime_listener = RealtimeListener()
