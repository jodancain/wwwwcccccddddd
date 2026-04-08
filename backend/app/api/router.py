from fastapi import APIRouter

from app.api.messages import router as messages_router
from app.api.contacts import router as contacts_router
from app.api.ai_chat import router as ai_chat_router
from app.api.media import router as media_router
from app.api.sync_routes import router as sync_router
from app.api.settings_routes import router as settings_router
from app.api.send import router as send_router

api_router = APIRouter()
api_router.include_router(messages_router)
api_router.include_router(contacts_router)
api_router.include_router(ai_chat_router)
api_router.include_router(media_router)
api_router.include_router(sync_router)
api_router.include_router(settings_router)
api_router.include_router(send_router)
