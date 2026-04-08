import json
from typing import AsyncIterator

import httpx
from loguru import logger

from app.ai.provider_base import AIProvider
from app.config.settings import get_settings


class GeminiProvider(AIProvider):
    def __init__(self):
        settings = get_settings()
        self.api_key = settings.GEMINI_API_KEY
        self.model = settings.GEMINI_MODEL
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    def _build_contents(self, messages: list[dict], system_prompt: str = "") -> tuple[list[dict], dict | None]:
        contents = []
        system_instruction = None

        if system_prompt:
            system_instruction = {"parts": [{"text": system_prompt}]}

        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}],
            })

        return contents, system_instruction

    async def chat(self, messages: list[dict], system_prompt: str = "") -> str:
        contents, system_instruction = self._build_contents(messages, system_prompt)

        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 4096,
            },
        }
        if system_instruction:
            body["systemInstruction"] = system_instruction

        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()

        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
        return ""

    async def chat_stream(self, messages: list[dict], system_prompt: str = "") -> AsyncIterator[str]:
        contents, system_instruction = self._build_contents(messages, system_prompt)

        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 4096,
            },
        }
        if system_instruction:
            body["systemInstruction"] = system_instruction

        url = f"{self.base_url}/models/{self.model}:streamGenerateContent?alt=sse&key={self.api_key}"

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for part in parts:
                                text = part.get("text", "")
                                if text:
                                    yield text
                    except json.JSONDecodeError:
                        continue
