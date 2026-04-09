"""Real-time WeChat message listener using wxauto (UIAutomation).

Polls WeChat's session list every 2 seconds. When new messages are detected,
reads them via GetAllMessage and pushes to the database + WebSocket.

This runs alongside the DB-decrypt sync engine for redundancy:
- wxauto: real-time (~2s delay), but only gets messages from visible UI
- DB decrypt: slower (~minutes delay), but gets ALL messages including history
"""
import asyncio
import time
import threading
from datetime import datetime
from typing import Optional

from loguru import logger

from app.config.settings import get_settings
from app.api.ws import ws_manager


class RealtimeListener:
    def __init__(self):
        self._running = False
        self._wx = None
        self._prev_sessions: dict = {}
        self._thread: Optional[threading.Thread] = None
        self._new_messages_queue: list[dict] = []
        self._lock = threading.Lock()
        self.status = "idle"
        self.connected = False
        self.error = ""

    def _init_wxauto(self) -> bool:
        """Initialize wxauto connection to WeChat (must run in thread)."""
        try:
            # Fix Windows charmap encoding before wxauto prints Chinese
            import sys, io, os
            os.environ["PYTHONIOENCODING"] = "utf-8"
            if hasattr(sys.stdout, 'buffer'):
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            if hasattr(sys.stderr, 'buffer'):
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

            from wxauto import WeChat
            self._wx = WeChat()
            self.connected = True
            self.error = ""
            logger.info("wxauto connected to WeChat")
            return True
        except Exception as e:
            self.error = str(e)
            self.connected = False
            logger.warning(f"wxauto init failed: {e}")
            return False

    def _poll_loop(self):
        """Background thread: poll WeChat session list for changes."""
        if not self._init_wxauto():
            return

        self.status = "listening"
        poll_interval = 2  # seconds

        while self._running:
            try:
                sessions = self._wx.GetSessionList()
                if sessions is None:
                    time.sleep(poll_interval)
                    continue

                current = dict(sessions)

                # Detect changes: new sessions or changed message counts
                changed_chats = []
                for name, count in current.items():
                    if name not in self._prev_sessions:
                        changed_chats.append(name)
                    elif current[name] != self._prev_sessions[name]:
                        changed_chats.append(name)

                # Also detect session order change (new messages push chats to top)
                current_order = list(current.keys())
                prev_order = list(self._prev_sessions.keys())
                if current_order and prev_order and current_order[0] != prev_order[0]:
                    top_chat = current_order[0]
                    if top_chat not in changed_chats:
                        changed_chats.append(top_chat)

                if changed_chats:
                    logger.info(f"wxauto: changes detected in {changed_chats}")
                    # Signal the async sync engine to do an immediate decrypt+sync
                    with self._lock:
                        for name in changed_chats:
                            self._new_messages_queue.append({
                                "chat_name": name,
                                "timestamp": time.time(),
                            })

                self._prev_sessions = current

            except Exception as e:
                logger.warning(f"wxauto poll error: {e}")
                # Try to reconnect
                time.sleep(5)
                try:
                    self._init_wxauto()
                except:
                    pass

            time.sleep(poll_interval)

        self.status = "stopped"

    def get_pending_changes(self) -> list[dict]:
        """Get and clear pending message change notifications."""
        with self._lock:
            changes = self._new_messages_queue.copy()
            self._new_messages_queue.clear()
        return changes

    async def start(self):
        """Start the real-time listener in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Realtime listener started")

    def stop(self):
        self._running = False
        self.status = "stopped"
        logger.info("Realtime listener stopped")

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "connected": self.connected,
            "status": self.status,
            "error": self.error,
        }


realtime_listener = RealtimeListener()
