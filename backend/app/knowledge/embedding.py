from __future__ import annotations

import math
import re
from hashlib import blake2b
from array import array
from typing import Iterable

import httpx

from app.config.settings import get_settings


def normalize_vector(values: Iterable[float]) -> list[float]:
    vector = [float(v) for v in values]
    norm = math.sqrt(sum(v * v for v in vector))
    if not norm:
        return vector
    return [v / norm for v in vector]


def pack_vector(values: Iterable[float]) -> bytes:
    data = array("f", normalize_vector(values))
    return data.tobytes()


def unpack_vector(blob: bytes) -> list[float]:
    data = array("f")
    data.frombytes(blob)
    return list(data)


def dot_score(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


class EmbeddingClient:
    def __init__(self):
        self.settings = get_settings()

    @property
    def api_key(self) -> str:
        return (self.settings.EMBEDDING_API_KEY or self.settings.OPENAI_API_KEY or "").strip()

    @property
    def base_url(self) -> str:
        return (self.settings.EMBEDDING_BASE_URL or self.settings.OPENAI_BASE_URL or "").rstrip("/")

    @property
    def model(self) -> str:
        return (self.settings.EMBEDDING_MODEL or "local-hash-768").strip()

    @property
    def configured(self) -> bool:
        if not self.settings.EMBEDDING_ENABLED:
            return False
        if self.model.startswith("local-hash"):
            return True
        return bool(self.api_key and self.base_url and self.model)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.configured:
            raise RuntimeError("Embedding API is not configured")
        if self.model.startswith("local-hash"):
            dimensions = self._local_dimensions()
            return [self._local_hash_embedding(text, dimensions) for text in texts]

        url = f"{self.base_url}/embeddings"
        payload = {"model": self.model, "input": texts}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json=payload, headers=headers)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = response.text[:500]
                raise RuntimeError(f"Embedding API failed: {response.status_code} {detail}") from exc
            data = response.json()
        items = sorted(data.get("data") or [], key=lambda item: int(item.get("index", 0)))
        vectors = [item.get("embedding") or [] for item in items]
        if len(vectors) != len(texts):
            raise RuntimeError(f"Embedding API returned {len(vectors)} vectors for {len(texts)} texts")
        return [normalize_vector(vector) for vector in vectors]

    def _local_dimensions(self) -> int:
        match = re.search(r"(\d+)$", self.model)
        if not match:
            return 768
        return max(128, min(int(match.group(1)), 4096))

    def _local_hash_embedding(self, text: str, dimensions: int) -> list[float]:
        vector = [0.0] * dimensions
        tokens = self._tokens(text)
        for token in tokens:
            digest = blake2b(token.encode("utf-8", errors="ignore"), digest_size=8).digest()
            value = int.from_bytes(digest, "little", signed=False)
            index = value % dimensions
            sign = 1.0 if (value >> 63) else -1.0
            weight = 1.0 + min(len(token), 8) * 0.08
            vector[index] += sign * weight
        return normalize_vector(vector)

    def _tokens(self, text: str) -> list[str]:
        lowered = text.lower()
        words = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]", lowered)
        tokens = list(words)
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]+", lowered))
        for size in (2, 3, 4):
            tokens.extend(chinese[i : i + size] for i in range(max(0, len(chinese) - size + 1)))
        for word in re.findall(r"[a-z0-9_]{3,}", lowered):
            tokens.extend(word[i : i + 3] for i in range(max(0, len(word) - 2)))
        return tokens or [lowered[:64]]


embedding_client = EmbeddingClient()
