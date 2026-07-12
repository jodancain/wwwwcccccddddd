from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.dependencies import get_db
from app.knowledge.embedding import embedding_client
from app.knowledge.indexer import knowledge_indexer
from app.knowledge.source_extractor import source_extractor


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(8, ge=1, le=30)
    talker: str = ""
    use_embedding: bool = True


@router.get("/status")
async def knowledge_status():
    db = await get_db()
    status = await db.get_knowledge_status()
    status["indexer"] = {
        "running": knowledge_indexer._running,
        "status": knowledge_indexer.status,
        "embedding_status": knowledge_indexer.embedding_status,
        "last_error": knowledge_indexer.last_error,
        "last_embedding_error": knowledge_indexer.last_embedding_error,
        "interval_seconds": knowledge_indexer.interval_seconds,
    }
    status["embedding"] = {
        "enabled": embedding_client.configured,
        "model": embedding_client.model,
        "base_url": embedding_client.base_url,
    }
    return status


@router.post("/index-now")
async def knowledge_index_now(limit: int = Query(5000, ge=1, le=50000)):
    return await knowledge_indexer.index_new_messages(limit=limit)


@router.post("/embed-now")
async def knowledge_embed_now(limit: int = Query(64, ge=1, le=256)):
    return await knowledge_indexer.embed_new_chunks(limit=limit)


@router.post("/enrich-now")
async def knowledge_enrich_now(
    hours: int = Query(24, ge=0, le=720),
    limit: int = Query(2000, ge=0, le=100000),
    max_links: int = Query(80, ge=0, le=500),
    max_images: int = Query(20, ge=0, le=100),
):
    db = await get_db()
    messages = await db.get_all_recent_messages(hours=hours, limit=limit)
    enriched = await source_extractor.enrich_messages(
        db,
        messages,
        max_links=max_links,
        max_images=max_images,
    )
    source_messages = [item for item in enriched if item.get("source_enrichments")]
    return {
        "status": "ok",
        "hours": hours,
        "messages_checked": len(messages),
        "messages_with_sources": len(source_messages),
        "source_enrichments": sum(len(item.get("source_enrichments") or []) for item in enriched),
    }


@router.post("/rebuild")
async def knowledge_rebuild(
    batch_limit: int = Query(5000, ge=500, le=50000),
    max_batches: int = Query(0, ge=0, le=1000),
    background: bool = Query(True),
):
    if background:
        asyncio.create_task(knowledge_indexer.rebuild(batch_limit=batch_limit, max_batches=max_batches))
        return {"status": "started", "background": True, "batch_limit": batch_limit, "max_batches": max_batches}
    return await knowledge_indexer.rebuild(batch_limit=batch_limit, max_batches=max_batches)


@router.post("/search")
async def knowledge_search(req: KnowledgeSearchRequest):
    db = await get_db()
    results = await db.search_knowledge(req.query, limit=req.limit, talker=req.talker)
    if req.use_embedding and embedding_client.configured:
        vectors = await embedding_client.embed_texts([req.query], input_type="query")
        vector_results = await db.search_knowledge_vector(
            vectors[0],
            model=embedding_client.model,
            limit=req.limit,
            talker=req.talker,
        )
        merged = {int(item.get("id") or 0): item for item in results}
        for item in vector_results:
            chunk_id = int(item.get("id") or 0)
            if chunk_id not in merged:
                merged[chunk_id] = item
        results = sorted(
            merged.values(),
            key=lambda item: (
                1 if item.get("retrieval") == "embedding" else 0,
                float(item.get("score") or 0),
                int(item.get("end_time") or 0),
            ),
            reverse=True,
        )[: req.limit]
    return {"query": req.query, "count": len(results), "items": results}
