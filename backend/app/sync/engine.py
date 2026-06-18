import asyncio
import time

from loguru import logger

from app.config.settings import get_settings
from app.dependencies import get_db
from app.wechat_reader.decryptor import WeChatDecryptor
from app.wechat_reader.parser import WeChatMessageParser
from app.api.ws import ws_manager


class SyncEngine:
    def __init__(self):
        self.settings = get_settings()
        self.decryptor = WeChatDecryptor()
        self.parser = WeChatMessageParser()
        self._running = False
        self._last_decrypt_time = 0
        self._force_decrypt = False
        self.status = "idle"

    async def start(self):
        self._running = True
        logger.info("SyncEngine started")

        # Start wxauto real-time listener
        try:
            from app.sync.realtime_listener import realtime_listener
            await realtime_listener.start()
            logger.info("Realtime listener started")
        except Exception as e:
            logger.warning(f"Realtime listener failed to start: {e}")

        # Initial decrypt
        await self._do_decrypt()

        # Run sync loop
        while self._running:
            try:
                now = time.time()

                # Check if wxauto detected new messages → force immediate decrypt
                try:
                    from app.sync.realtime_listener import realtime_listener
                    changes = realtime_listener.get_pending_changes()
                    if changes:
                        chat_names = [c["chat_name"] for c in changes]
                        logger.info(f"wxauto detected new messages in: {chat_names}")
                        self._force_decrypt = True
                        # Broadcast immediate notification to frontend
                        await ws_manager.broadcast("realtime_update", {
                            "chats": chat_names,
                            "timestamp": time.time(),
                        })
                except Exception:
                    pass

                # Decrypt: on schedule OR when wxauto triggers it
                if self._force_decrypt or (now - self._last_decrypt_time >= self.settings.DECRYPT_INTERVAL_SECONDS):
                    self._force_decrypt = False
                    await self._do_decrypt()

                # Sync messages every cycle
                await self._do_sync()

            except Exception as e:
                logger.error(f"Sync cycle error: {e}")
                self.status = f"error: {e}"

            await asyncio.sleep(self.settings.SYNC_INTERVAL_SECONDS)

    def stop(self):
        self._running = False
        self.status = "stopped"
        try:
            from app.sync.realtime_listener import realtime_listener
            realtime_listener.stop()
        except:
            pass
        logger.info("SyncEngine stopped")

    async def _do_decrypt(self):
        self.status = "decrypting"
        try:
            result = await asyncio.to_thread(self.decryptor.decrypt_databases)
            self._last_decrypt_time = time.time()
            logger.info(f"Decrypt done: {result}")
            # Propagate the self-wxid from the db_storage path → parser, so
            # is_sender gets set correctly on outbound messages.
            src_dir = self.decryptor.source_db_dir() or ""
            if src_dir:
                import os, re
                parts = os.path.normpath(src_dir).split(os.sep)
                for i, p in enumerate(parts):
                    if p.startswith("wxid_") and i + 1 < len(parts) and parts[i + 1] == "db_storage":
                        wxid = re.sub(r"_[a-z0-9]+$", "", p)
                        self.parser.set_self_wxid(wxid)
                        break
        except Exception as e:
            logger.error(f"Decrypt failed: {e}")
            self._last_decrypt_time = time.time()
        finally:
            self.status = "idle"

    async def _do_sync(self):
        self.status = "syncing"
        try:
            db = await get_db()
            sync_state = await db.get_sync_state()
            last_ts = sync_state.get("last_sync_timestamp", 0)

            # Parse contacts
            contacts = await asyncio.to_thread(self.parser.get_contacts)
            if contacts:
                contact_dicts = [
                    {"username": c.username, "nickname": c.nickname,
                     "remark": c.remark, "alias": c.alias, "is_group": c.is_group}
                    for c in contacts
                ]
                await db.bulk_upsert_contacts(contact_dicts)

            # Parse messages
            messages = await asyncio.to_thread(self.parser.get_messages, last_ts)
            if messages:
                msg_dicts = [m.to_dict() for m in messages]
                inserted = await db.bulk_insert_messages(msg_dicts)

                if inserted > 0:
                    max_ts = max(m.create_time for m in messages)
                    await db.update_sync_state(max_ts, inserted)
                    await ws_manager.broadcast("new_messages", {
                        "count": inserted,
                        "timestamp": max_ts,
                    })
                    logger.info(f"Synced {inserted} new messages")

            self.status = "idle"

        except Exception as e:
            logger.error(f"Sync failed: {e}")
            self.status = f"sync_error: {e}"

    async def force_sync(self):
        await self._do_decrypt()
        await self._do_sync()


sync_engine = SyncEngine()
