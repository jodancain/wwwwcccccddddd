from abc import ABC, abstractmethod
from typing import AsyncIterator


class AIProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict], system_prompt: str = "") -> str:
        ...

    @abstractmethod
    async def chat_stream(self, messages: list[dict], system_prompt: str = "") -> AsyncIterator[str]:
        ...
