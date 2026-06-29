from __future__ import annotations

import base64
import json
import re
from html.parser import HTMLParser
from typing import Any

import httpx
from loguru import logger

from app.config.settings import get_settings
from app.wechat_reader.media_resolver import WeChatMediaResolver


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self._in_title = False
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        low = tag.lower()
        if low in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if low == "title":
            self._in_title = True
        if low == "meta":
            data = {k.lower(): v or "" for k, v in attrs}
            name = (data.get("name") or data.get("property") or "").lower()
            if name in {"description", "og:description", "twitter:description"} and data.get("content"):
                self.meta_description = data["content"].strip()

    def handle_endtag(self, tag: str):
        low = tag.lower()
        if low in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if low == "title":
            self._in_title = False

    def handle_data(self, data: str):
        text = re.sub(r"\s+", " ", data or "").strip()
        if not text:
            return
        if self._in_title:
            self.title += text
            return
        if self._skip_depth:
            return
        if len(text) >= 8:
            self.parts.append(text)

    def summary_text(self, limit: int = 2400) -> str:
        body = " ".join(self.parts)
        body = re.sub(r"\s+", " ", body).strip()
        return body[:limit]


class SourceExtractor:
    def __init__(self):
        self.settings = get_settings()
        self.media_resolver = WeChatMediaResolver()

    async def enrich_messages(self, db: Any, messages: list[dict], *, max_links: int = 30, max_images: int = 8) -> list[dict]:
        link_count = 0
        image_count = 0
        for item in messages:
            enrichments = []
            for source in self._sources_from_message(item):
                message_id = int(item.get("id") or 0)
                if message_id:
                    cached = await db.get_source_enrichment(message_id, source["kind"], source["key"])
                    if cached and not self._should_retry_cached(source, cached):
                        enrichments.append(cached)
                        continue
                if source["kind"] == "link":
                    if link_count >= max_links:
                        continue
                    link_count += 1
                if source["kind"] == "image":
                    if image_count >= max_images:
                        continue
                    image_count += 1
                enriched = await self._get_or_create_enrichment(db, item, source)
                if enriched:
                    enrichments.append(enriched)
            item["source_enrichments"] = enrichments
        return messages

    def _sources_from_message(self, item: dict) -> list[dict]:
        out: list[dict] = []
        content = item.get("content") or ""
        display = self._json_display(item.get("display_content") or "")

        if int(item.get("type") or 0) == 49 or content.startswith("[链接]"):
            url = (display.get("url") or "").strip()
            if url:
                out.append({"kind": "link", "key": url, "meta": display})

        for url in re.findall(r"https?://[^\s<>'\"，。！？）)]+", content):
            out.append({"kind": "link", "key": url, "meta": {"url": url}})

        if int(item.get("type") or 0) == 3 or content.startswith("[图片]"):
            local_id = int(item.get("wechat_local_id") or 0)
            key = str(local_id or item.get("id") or "")
            out.append({"kind": "image", "key": key, "meta": display})

        seen = set()
        unique = []
        for source in out:
            marker = (source["kind"], source["key"])
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(source)
        return unique

    def _json_display(self, value: str) -> dict:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    async def _get_or_create_enrichment(self, db: Any, item: dict, source: dict) -> dict | None:
        message_id = int(item.get("id") or 0)
        if not message_id:
            return None
        cached = await db.get_source_enrichment(message_id, source["kind"], source["key"])
        if cached and not self._should_retry_cached(source, cached):
            return cached
        try:
            if source["kind"] == "link":
                text, metadata = await self._extract_link(source["key"], source.get("meta") or {})
            else:
                text, metadata = await self._extract_image(item, source.get("meta") or {})
            status = self._status_for_extracted(source["kind"], text, metadata)
            return await db.upsert_source_enrichment(
                message_id=message_id,
                kind=source["kind"],
                source_key=source["key"],
                extracted_text=text,
                metadata=metadata,
                status=status,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Source enrichment failed: {source['kind']} {source['key']}: {exc}")
            return await db.upsert_source_enrichment(
                message_id=message_id,
                kind=source["kind"],
                source_key=source["key"],
                extracted_text="",
                metadata=source.get("meta") or {},
                status="error",
                error=str(exc)[:500],
            )

    def _should_retry_cached(self, source: dict, cached: dict) -> bool:
        status = cached.get("status") or ""
        if status in {"error", "empty", "missing_media"}:
            return True
        if source.get("kind") == "image":
            metadata = cached.get("metadata") or {}
            text = cached.get("extracted_text") or ""
            if metadata.get("vision") is False and "暂未解析到本地图片" in text:
                return True
        return False

    def _status_for_extracted(self, kind: str, text: str, metadata: dict) -> str:
        if not text:
            return "empty"
        if kind == "image" and metadata.get("missing_media"):
            return "missing_media"
        return "ok"

    async def _extract_link(self, url: str, meta: dict) -> tuple[str, dict]:
        pieces = []
        title = meta.get("title") or ""
        desc = meta.get("des") or meta.get("description") or ""
        source = meta.get("source") or ""
        if title:
            pieces.append(f"链接标题：{title}")
        if desc:
            pieces.append(f"链接描述：{desc}")
        if source:
            pieces.append(f"来源：{source}")
        pieces.append(f"URL：{url}")

        fetched = await self._fetch_web_text(url)
        if fetched:
            pieces.append(f"网页正文摘要：{fetched}")
        return "\n".join(pieces), {**meta, "url": url, "fetched": bool(fetched)}

    async def _fetch_web_text(self, url: str) -> str:
        async with httpx.AsyncClient(
            timeout=12,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 WeChatAI/1.0"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return f"非 HTML 内容，类型：{content_type or 'unknown'}"
            html = response.text[:1_000_000]
        parser = _HTMLTextExtractor()
        parser.feed(html)
        parts = []
        if parser.title:
            parts.append(f"网页标题：{parser.title[:200]}")
        if parser.meta_description:
            parts.append(f"网页描述：{parser.meta_description[:500]}")
        body = parser.summary_text()
        if body:
            parts.append(body)
        return "\n".join(parts)[:3000]

    async def _extract_image(self, item: dict, meta: dict) -> tuple[str, dict]:
        local_id = int(item.get("wechat_local_id") or 0)
        raw, mime = self.media_resolver.load_image_bytes(local_id) if local_id else (None, None)
        if not raw or not mime:
            return ("图片消息：暂未解析到本地图片文件。", {**meta, "local_id": local_id, "vision": False, "missing_media": True})
        if not self.settings.OPENAI_API_KEY or not self.settings.OPENAI_BASE_URL:
            return ("图片消息：已定位本地图片，但未配置视觉模型，暂不能生成图片描述。", {**meta, "local_id": local_id, "vision": False})

        if len(raw) > 8 * 1024 * 1024:
            return (
                "图片消息：已定位本地图片，但文件超过 8MB，暂未送入视觉模型解析。",
                {**meta, "local_id": local_id, "mime": mime, "vision": False, "too_large": True},
            )

        encoded = base64.b64encode(raw).decode("ascii")
        data_url = f"data:{mime};base64,{encoded}"
        payload = {
            "model": self.settings.OPENAI_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请用中文提取这张微信图片里的可用信息：先做OCR文字识别，再描述图片内容、人物/物品/截图主题、可能需要用户关注的点。控制在300字内。",
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_tokens": 800,
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                self.settings.OPENAI_BASE_URL.rstrip("/") + "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return f"图片解析：{text.strip()}", {**meta, "local_id": local_id, "mime": mime, "vision": True}


source_extractor = SourceExtractor()
