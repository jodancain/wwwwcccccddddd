from fastapi import APIRouter, Query
from fastapi.responses import Response

from app.wechat_reader.media_resolver import WeChatMediaResolver

router = APIRouter(prefix="/media", tags=["media"])

_resolver: WeChatMediaResolver | None = None


def get_resolver() -> WeChatMediaResolver:
    global _resolver
    if _resolver is None:
        _resolver = WeChatMediaResolver()
    return _resolver


@router.get("/image/{local_id}")
async def get_image(local_id: int):
    resolver = get_resolver()
    image_bytes, mime_type = resolver.load_image_bytes(local_id)
    if image_bytes:
        return Response(content=image_bytes, media_type=mime_type)
    return Response(status_code=404, content=b"Image not found")
