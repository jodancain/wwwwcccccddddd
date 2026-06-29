import asyncio
import time

from loguru import logger

from app.api.ws import ws_manager
from app.config.settings import get_settings
from app.dependencies import get_db
from app.wechat_reader.decryptor import WeChatDecryptor
from app.wechat_reader.parser import WeChatMessageParser


class SyncEngine:
    def __init__(self):
        self.settings = get_settings()
        self.decryptor = WeChatDecryptor()
        self.parser = WeChatMessageParser()
        self._running = False
        self._last_decrypt_time = 0
        self._last_contacts_sync_time = time.time()
        self._cycle_lock = asyncio.Lock()
        self.status = "idle"

    async def start(self):
        if self._running:
            logger.info("SyncEngine already running")
            return

        self._running = True
        logger.info("SyncEngine started")

        try:
            from app.sync.realtime_listener import realtime_listener

            await realtime_listener.start()
            logger.info("Realtime listener started")
        except Exception as e:
            logger.warning(f"Realtime listener failed to start: {e}")

        db = await get_db()
        has_local_messages = await db.get_message_count() > 0
        if has_local_messages:
            logger.info("Skipping startup decrypt; local message cache is already populated")
            self._last_decrypt_time = time.time()
            self.status = "idle"
        else:
            await self._run_cycle(decrypt=True, sync=True)

        while self._running:
            try:
                now = time.time()
                should_decrypt = (
                    now - self._last_decrypt_time
                    >= self.settings.DECRYPT_INTERVAL_SECONDS
                )

                try:
                    from app.sync.realtime_listener import realtime_listener

                    changes = realtime_listener.get_pending_changes()
                    if changes:
                        chat_names = [c["chat_name"] for c in changes]
                        logger.info(f"wxauto detected new messages in: {chat_names}")
                        min_force_gap = max(
                            60,
                            min(120, self.settings.DECRYPT_INTERVAL_SECONDS),
                        )
                        if now - self._last_decrypt_time >= min_force_gap:
                            should_decrypt = True
                        else:
                            logger.info("Deferring realtime decrypt; recent decrypt is still fresh")
                        await ws_manager.broadcast(
                            "realtime_update",
                            {
                                "chats": chat_names,
                                "timestamp": time.time(),
                            },
                        )
                except Exception:
                    pass

                if should_decrypt:
                    await self._run_cycle(decrypt=True, sync=True)
                else:
                    self.status = "idle"

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
        except Exception:
            pass
        logger.info("SyncEngine stopped")

    async def _run_cycle(self, *, decrypt: bool, sync: bool = True):
        async with self._cycle_lock:
            if decrypt:
                await self._do_decrypt()
            if sync:
                await self._do_sync()

    async def _do_decrypt(self):
        self.status = "decrypting"
        try:
            result = await asyncio.to_thread(self.decryptor.decrypt_databases)
            self._last_decrypt_time = time.time()
            logger.info(f"Decrypt done: {result}")
            src_dir = self.decryptor.source_db_dir() or ""
            if src_dir:
                import os
                import re

                parts = os.path.normpath(src_dir).split(os.sep)
                for i, p in enumerate(parts):
                    if (
                        p.startswith("wxid_")
                        and i + 1 < len(parts)
                        and parts[i + 1] == "db_storage"
                    ):
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

            contact_count = await db.get_contact_count()
            now = time.time()
            should_sync_contacts = (
                contact_count == 0
                or now - self._last_contacts_sync_time >= 300
            )
            if should_sync_contacts:
                contacts = await asyncio.to_thread(self.parser.get_contacts)
                if contacts:
                    contact_dicts = [
                        {
                            "username": c.username,
                            "nickname": c.nickname,
                            "remark": c.remark,
                            "alias": c.alias,
                            "is_group": c.is_group,
                        }
                        for c in contacts
                    ]
                    await db.bulk_upsert_contacts(contact_dicts)
                    self._last_contacts_sync_time = now
            else:
                logger.debug("Skipping contact refresh; cached contacts are still fresh")

            messages = await asyncio.to_thread(self.parser.get_messages, last_ts)
            if messages:
                msg_dicts = [m.to_dict() for m in messages]
                inserted = await db.bulk_insert_messages(msg_dicts)

                if inserted > 0:
                    max_ts = max(m.create_time for m in messages)
                    await db.update_sync_state(max_ts, inserted)
                    await ws_manager.broadcast(
                        "new_messages",
                        {
                            "count": inserted,
                            "timestamp": max_ts,
                        },
                    )
                    logger.info(f"Synced {inserted} new messages")

                    try:
                        from app.knowledge.indexer import knowledge_indexer

                        index_result = await knowledge_indexer.index_new_messages(limit=max(5000, inserted * 2))
                        logger.info(f"Knowledge index updated after sync: {index_result}")
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"Knowledge indexing after sync failed: {e}")

                    try:
                        from app.agent.service import wechat_agent_service

                        result = await wechat_agent_service.process_entry_messages()
                        if result.get("status") not in {"disabled", "idle", "skipped"}:
                            logger.info(f"WeChat agent processed sync update: {result}")
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"WeChat agent processing failed: {e}")

            self.status = "idle"

        except Exception as e:
            logger.error(f"Sync failed: {e}")
            self.status = f"sync_error: {e}"

    async def force_sync(self):
        await self._run_cycle(decrypt=True)


sync_engine = SyncEngine()
