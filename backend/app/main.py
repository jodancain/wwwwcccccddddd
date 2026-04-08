import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pathlib import Path

from app.config.settings import get_settings
from app.dependencies import get_db, close_db
from app.api.router import api_router
from app.api.ws import ws_manager
from app.sync.engine import sync_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    logger.add(
        str(settings.log_dir / "app_{time}.log"),
        rotation="1 day",
        retention="7 days",
        encoding="utf-8",
    )
    logger.info("Starting WeChatAI...")

    # Init database
    await get_db()

    # Start sync engine in background
    sync_task = asyncio.create_task(sync_engine.start())

    yield

    # Shutdown
    sync_engine.stop()
    sync_task.cancel()
    await close_db()
    logger.info("WeChatAI stopped.")


app = FastAPI(
    title="WeChatAI - Cursor for WeChat",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# Serve frontend static files (production build)
frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
