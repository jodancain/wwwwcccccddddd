"""Training pipeline API routes."""
import asyncio

from fastapi import APIRouter
from loguru import logger

from app.dependencies import get_db
from app.training.data_exporter import export_training_data, get_export_stats
from app.training.pipeline import pipeline

router = APIRouter(prefix="/training", tags=["training"])


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
    """Start inference server (standalone)."""
    await pipeline._start_inference_server()
    return pipeline.get_status()


@router.post("/stop-server")
async def stop_server():
    """Stop inference server."""
    await pipeline.stop_inference()
    return {"message": "Server stopped"}
