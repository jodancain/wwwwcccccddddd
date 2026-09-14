from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import AsyncIterator

from app.ai.provider_base import AIProvider
from app.config.settings import get_settings


class AnthropicProvider(AIProvider):
    """Small Messages API client used when the primary model gateway fails."""

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.ANTHROPIC_API_KEY
        self.base_url = settings.ANTHROPIC_BASE_URL.rstrip("/")
        self.model = settings.CLAUDE_MODEL

    async def chat(self, messages: list[dict], system_prompt: str = "") -> str:
        return await asyncio.to_thread(self._chat_sync, messages, system_prompt)

    async def chat_stream(self, messages: list[dict], system_prompt: str = "") -> AsyncIterator[str]:
        text = await self.chat(messages, system_prompt)
        if text:
            yield text

    def _chat_sync(self, messages: list[dict], system_prompt: str) -> str:
        if not self.api_key or not self.base_url:
            raise RuntimeError("Anthropic API is not configured")

        normalized_messages = []
        for message in messages:
            role = "assistant" if message.get("role") == "assistant" else "user"
            normalized_messages.append({"role": role, "content": str(message.get("content") or "")})

        body = {
            "model": self.model,
            "max_tokens": 16000,
            "messages": normalized_messages,
        }
        if system_prompt:
            body["system"] = system_prompt

        request = urllib.request.Request(
            f"{self.base_url}/messages",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=420) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1000]
            raise RuntimeError(f"Anthropic HTTP {exc.code}: {detail}") from exc

        parts = [
            str(block.get("text"))
            for block in payload.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        ]
        return "\n".join(parts).strip()
