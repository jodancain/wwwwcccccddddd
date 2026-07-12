from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from loguru import logger

from app.config.settings import get_settings
from app.dependencies import get_db
from app.knowledge.embedding import embedding_client
from app.knowledge.source_extractor import source_extractor


MAX_CHUNK_CHARS = 2600
MAX_CHUNK_MESSAGES = 45


def _display_name(item: dict[str, Any]) -> str:
    return item.get("remark") or item.get("nickname") or item.get("alias") or item.get("talker") or ""


def _message_text(item: dict[str, Any]) -> str:
    text = (item.get("content") or item.get("display_content") or "").strip()
    if not text:
        type_name = item.get("type_name") or "message"
        text = f"[{type_name}]"
    extras = []
    for enriched in item.get("source_enrichments") or []:
        if enriched.get("status") != "ok":
            continue
        extracted = re.sub(r"\s+", " ", enriched.get("extracted_text") or "").strip()
        if not extracted:
            continue
        label = "link" if enriched.get("kind") == "link" else "image"
        extras.append(f"[{label} parsed] {extracted[:1200]}")
    if extras:
        text = text + "\n" + "\n".join(extras)
    return re.sub(r"\s+", " ", text)


def _speaker(item: dict[str, Any]) -> str:
    if int(item.get("is_sender") or 0):
        return "我"
    sender = item.get("sender") or ""
    return sender or _display_name(item) or "对方"


def _chunk_title(items: list[dict[str, Any]]) -> str:
    first = items[0]
    last = items[-1]
    name = _display_name(first)
    if first.get("create_date") == last.get("create_date"):
        return f"{name} {first.get('create_date')}"
    return f"{name} {first.get('create_date')} 至 {last.get('create_date')}"


def build_message_chunks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    current_talker = ""

    def flush():
        nonlocal current, current_chars, current_talker
        if not current:
            return
        first = current[0]
        last = current[-1]
        lines = []
        for item in current:
            lines.append(
                f"{item.get('create_date', '')} {item.get('create_time', '')} "
                f"{_speaker(item)}: {_message_text(item)}"
            )
        metadata = {
            "name": _display_name(first),
            "is_group": bool(first.get("is_group")),
            "first_date": first.get("create_date", ""),
            "last_date": last.get("create_date", ""),
        }
        chunks.append(
            {
                "source": "wechat",
                "talker": first.get("talker", ""),
                "title": _chunk_title(current),
                "text": "\n".join(lines),
                "start_message_id": int(first.get("id") or 0),
                "end_message_id": int(last.get("id") or 0),
                "start_time": int(first.get("create_time") or 0),
                "end_time": int(last.get("create_time") or 0),
                "message_count": len(current),
                "metadata": metadata,
            }
        )
        current = []
        current_chars = 0
        current_talker = ""

    ordered_messages = sorted(
        messages,
        key=lambda item: (
            item.get("talker", ""),
            item.get("create_date", ""),
            int(item.get("create_time") or 0),
            int(item.get("id") or 0),
        ),
    )

    for item in ordered_messages:
        text = _message_text(item)
        talker = item.get("talker", "")
        date = item.get("create_date", "")
        current_date = current[-1].get("create_date", "") if current else date
        should_flush = (
            bool(current)
            and (
                talker != current_talker
                or date != current_date
                or len(current) >= MAX_CHUNK_MESSAGES
                or current_chars + len(text) > MAX_CHUNK_CHARS
            )
        )
        if should_flush:
            flush()
        current.append(item)
        current_talker = talker
        current_chars += len(text)
    flush()
    return chunks


class KnowledgeIndexer:
    def __init__(self):
        self._running = False
        self._task_lock = asyncio.Lock()
        self._embedding_lock = asyncio.Lock()
        self.interval_seconds = 30
        self.status = "idle"
        self.embedding_status = "idle"
        self.last_error = ""
        self.last_embedding_error = ""

    async def start(self):
        if self._running:
            return
        self._running = True
        logger.info("Knowledge indexer started")
        while self._running:
            try:
                await self.index_new_messages(limit=5000)
                await self.embed_new_chunks()
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                self.status = f"error: {exc}"
                logger.warning(f"Knowledge indexing failed: {exc}")
            await asyncio.sleep(self.interval_seconds)

    def stop(self):
        self._running = False
        self.status = "stopped"
        logger.info("Knowledge indexer stopped")

    async def index_new_messages(self, limit: int = 5000) -> dict:
        async with self._task_lock:
            started = time.time()
            self.status = "indexing"
            db = await get_db()
            last_raw = await db.get_setting("knowledge.last_indexed_message_id", "0")
            last_id = int(last_raw or 0)
            messages = await db.get_messages_after_id(last_id, limit=limit)
            if not messages:
                self.status = "idle"
                return {"status": "idle", "indexed_chunks": 0, "processed_messages": 0, "last_indexed_message_id": last_id}

            messages = await source_extractor.enrich_messages(
                db,
                messages,
                max_links=max(0, int(settings.KNOWLEDGE_REALTIME_MAX_LINKS or 0)),
                max_images=max(0, int(settings.KNOWLEDGE_REALTIME_MAX_IMAGES or 0)),
            )
            chunks = build_message_chunks(messages)
            inserted = await db.insert_knowledge_chunks(chunks)
            max_id = max(int(item.get("id") or 0) for item in messages)
            await db.set_setting("knowledge.last_indexed_message_id", str(max_id), "Knowledge index high-water mark")
            embedding_result = await self.embed_new_chunks()
            elapsed_ms = int((time.time() - started) * 1000)
            self.status = "idle"
            result = {
                "status": "indexed",
                "indexed_chunks": inserted,
                "processed_messages": len(messages),
                "last_indexed_message_id": max_id,
                "embedding": embedding_result,
                "elapsed_ms": elapsed_ms,
            }
            logger.info(f"Knowledge indexed: {result}")
            return result

    async def rebuild(self, batch_limit: int = 5000, max_batches: int = 0) -> dict:
        async with self._task_lock:
            db = await get_db()
            await db.clear_knowledge_chunks()
        total_chunks = 0
        total_messages = 0
        batches = 0
        while True:
            result = await self.index_new_messages(limit=batch_limit)
            if result.get("status") == "idle":
                break
            total_chunks += int(result.get("indexed_chunks") or 0)
            total_messages += int(result.get("processed_messages") or 0)
            batches += 1
            if max_batches and batches >= max_batches:
                break
        return {
            "status": "rebuilt" if not max_batches or batches < max_batches else "partial",
            "batches": batches,
            "indexed_chunks": total_chunks,
            "processed_messages": total_messages,
            "indexer_status": self.status,
        }

    async def embed_new_chunks(self, limit: int | None = None) -> dict:
        settings = get_settings()
        if not embedding_client.configured:
            self.embedding_status = "disabled"
            return {"status": "disabled", "embedded_chunks": 0, "reason": "embedding API is not configured"}

        batch_limit = limit or max(1, min(int(settings.EMBEDDING_BATCH_SIZE or 64), 256))
        async with self._embedding_lock:
            started = time.time()
            self.embedding_status = "embedding"
            db = await get_db()
            chunks = await db.get_chunks_without_embedding(embedding_client.model, limit=batch_limit)
            if not chunks:
                self.embedding_status = "idle"
                return {"status": "idle", "embedded_chunks": 0, "model": embedding_client.model}

            texts = [self._embedding_text(item) for item in chunks]
            try:
                vectors = await embedding_client.embed_texts(texts, input_type="document")
            except Exception as exc:  # noqa: BLE001
                self.last_embedding_error = str(exc)
                self.embedding_status = f"error: {exc}"
                logger.warning(f"Knowledge embedding failed: {exc}")
                return {"status": "error", "embedded_chunks": 0, "error": str(exc), "model": embedding_client.model}

            records = [
                {"chunk_id": int(chunk.get("id") or 0), "vector": vector}
                for chunk, vector in zip(chunks, vectors)
            ]
            inserted = await db.insert_knowledge_embeddings(embedding_client.model, records)
            elapsed_ms = int((time.time() - started) * 1000)
            self.embedding_status = "idle"
            result = {
                "status": "embedded",
                "embedded_chunks": inserted,
                "model": embedding_client.model,
                "elapsed_ms": elapsed_ms,
            }
            logger.info(f"Knowledge embedded: {result}")
            return result

    def _embedding_text(self, item: dict[str, Any]) -> str:
        title = item.get("title") or ""
        talker = item.get("talker") or ""
        text = item.get("text") or ""
        return f"{title}\n{talker}\n{text}"[:8000]


knowledge_indexer = KnowledgeIndexer()
