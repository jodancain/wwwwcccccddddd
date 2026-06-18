"""Training pipeline API routes."""
import asyncio
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from app.dependencies import get_db
from app.training.data_exporter import export_training_data, get_export_stats
from app.training.model_registry import registry
from app.training.pipeline import pipeline

router = APIRouter(prefix="/training", tags=["training"])


class MyModelReplyRequest(BaseModel):
    talker: str


class ImportModelRequest(BaseModel):
    path: str
    name: str = ""
    base_path: str = ""
    model_type: str = ""  # "" = auto-detect, or "full" / "lora"
    notes: str = ""


class ScanModelsRequest(BaseModel):
    roots: Optional[list[str]] = None


@router.get("/stats")
async def training_stats():
    """Get stats about available training data."""
    db = await get_db()
    return await get_export_stats(db)


@router.get("/status")
async def training_status():
    """Get current pipeline status."""
    return pipeline.get_status()


@router.post("/start")
async def start_training():
    """Start the full one-click training pipeline."""
    if pipeline._running:
        return {"error": "Pipeline already running", "status": pipeline.get_status()}

    db = await get_db()
    # Run pipeline in background
    asyncio.create_task(pipeline.run_full_pipeline(db))
    return {"message": "Pipeline started", "status": pipeline.get_status()}


@router.post("/stop")
async def stop_training():
    """Stop the current training."""
    pipeline.stop_training()
    return {"message": "Training stopped"}


@router.post("/export-data")
async def export_data():
    """Export chat data for training (standalone, without full pipeline)."""
    db = await get_db()
    result = await export_training_data(db)
    return result


@router.post("/start-server")
async def start_server():
    """Start inference server using whichever model is currently active."""
    try:
        await pipeline.start_inference_from_registry()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return pipeline.get_status()


@router.post("/stop-server")
async def stop_server():
    """Stop inference server."""
    await pipeline.stop_inference()
    return {"message": "Server stopped"}


# ---------------------------------------------------------------------------
# Model management: import / list / scan / activate / delete
# ---------------------------------------------------------------------------


@router.get("/models")
async def list_registered_models():
    """List all imported models + which one is active + inference status."""
    missing_deps = pipeline.missing_inference_deps()
    return {
        "models": registry.list_models(),
        "active": registry.get_active(),
        "inference": {
            "running": pipeline._check_inference_alive(),
            "port": pipeline.inference_port,
            "missing_deps": missing_deps,
            "deps_ok": not missing_deps,
        },
    }


@router.post("/models/scan")
async def scan_models(req: ScanModelsRequest):
    """Walk known model directories and return found full/LoRA dirs.

    If ``roots`` is empty, the default roots (``D:/WeChatAI_models/``,
    ``data/models/``) are used.
    """
    try:
        found = registry.scan_roots(req.roots)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    return {"found": found}


@router.post("/models/import")
async def import_model(req: ImportModelRequest):
    """Register an existing model directory.

    The body maps 1-to-1 to :meth:`ModelRegistry.add_model`. ``model_type``
    may be left blank to auto-detect (recommended). For a LoRA adapter,
    ``base_path`` defaults to the ``base_model_name_or_path`` field of the
    adapter config but can be overridden if you want to swap the base.
    """
    try:
        entry = registry.add_model(
            path=req.path,
            name=req.name,
            base_path=req.base_path,
            model_type=req.model_type,
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("import_model failed")
        raise HTTPException(status_code=500, detail=str(e))
    return {"model": entry}


@router.post("/models/{model_id}/activate")
async def activate_model(model_id: str):
    """Mark a model as active and (re)start the inference server with it."""
    entry = registry.set_active(model_id)
    if not entry:
        raise HTTPException(status_code=404, detail="model not found")
    try:
        await pipeline.start_inference_from_registry()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("activate_model failed")
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "active": registry.get_active(),
        "inference": {
            "running": pipeline._check_inference_alive(),
            "port": pipeline.inference_port,
        },
    }


@router.delete("/models/{model_id}")
async def delete_model(model_id: str):
    """Unregister a model. Does NOT delete files on disk."""
    if not registry.remove(model_id):
        raise HTTPException(status_code=404, detail="model not found")
    return {"ok": True}


@router.post("/my-reply")
async def my_model_reply(req: MyModelReplyRequest):
    """Use the personal model to generate a reply for a conversation."""
    if not pipeline._check_inference_alive():
        return {"error": "分身模型未运行，请先训练并部署"}

    db = await get_db()
    # Get recent messages for context (100+ messages for better understanding)
    recent = await db.get_recent_messages_for_talker(req.talker, limit=100)
    if not recent:
        return {"error": "没有找到对话记录"}

    # Build chat messages for the model
    messages = []
    for msg in recent:
        role = "assistant" if msg.get("is_sender") else "user"
        content = msg.get("content", "")
        if content and not content.startswith("["):
            messages.append({"role": role, "content": content})

    # Ensure last message is from "user" (the other person)
    if not messages or messages[-1]["role"] != "user":
        return {"error": "最后一条消息不是对方发的，无需回复"}

    # Keep last 50 turns for richer context
    messages = messages[-50:]

    # Call personal model API
    try:
        api_url = f"http://localhost:{pipeline.inference_port}/v1/chat/completions"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(api_url, json={
                "model": "my-style",
                "messages": messages,
                "temperature": 0.8,
                "max_tokens": 256,
            })
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"].strip()
            # Clean up any special tokens
            reply = reply.replace("<|im_end|>", "").replace("<|im_start|>", "").strip()
            return {"reply": reply, "context_turns": len(messages)}
    except Exception as e:
        logger.error(f"My model reply error: {e}")
        return {"error": f"分身模型调用失败: {str(e)}"}
