from typing import AsyncIterator

from loguru import logger

from app.ai.provider_base import AIProvider
from app.config.settings import get_settings


class OpenAIProvider(AIProvider):
    def __init__(self):
        settings = get_settings()
        self.api_key = settings.OPENAI_API_KEY
        self.base_url = settings.OPENAI_BASE_URL
        self.model = settings.OPENAI_MODEL

    def _get_client(self):
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    def _build_messages(self, messages: list[dict], system_prompt: str = "") -> list[dict]:
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        for msg in messages:
            result.append({"role": msg["role"], "content": msg["content"]})
        return result

    async def chat(self, messages: list[dict], system_prompt: str = "") -> str:
        client = self._get_client()
        msgs = self._build_messages(messages, system_prompt)

        response = await client.chat.completions.create(
            model=self.model,
            messages=msgs,
            temperature=0.7,
            max_tokens=4096,
        )
        return response.choices[0].message.content or ""

    async def chat_stream(self, messages: list[dict], system_prompt: str = "") -> AsyncIterator[str]:
        client = self._get_client()
        msgs = self._build_messages(messages, system_prompt)

        stream = await client.chat.completions.create(
            model=self.model,
            messages=msgs,
            temperature=0.7,
            max_tokens=4096,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
