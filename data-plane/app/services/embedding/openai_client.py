"""OpenAI embedding client — calls the OpenAI API for dense embeddings (text-embedding-3-small)."""

import time

import httpx

from app.config import ext
from app.services.embedding.bge_m3_client import EmbeddingError, EmbeddingResult
from app.utils.logger import get_logger

log = get_logger(__name__)

OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"
DEFAULT_MODEL = "text-embedding-3-small"


class OpenAIEmbedClient:
    """HTTP client for OpenAI embeddings API. Drop-in replacement for BGEM3Client."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._client: httpx.AsyncClient | None = None
        self._model = model
        self._api_key = ext.openai_api_key

    async def startup(self) -> None:
        if not self._api_key:
            log.warning("openai_embed_client_no_key")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60))
        log.info("openai_embed_client_started", model=self._model)

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
        log.info("openai_embed_client_stopped")

    async def check_health(self) -> bool:
        return bool(self._api_key)

    async def embed(self, text: str) -> EmbeddingResult:
        """Generate dense embeddings for a single text."""
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Generate embeddings for a batch of texts via OpenAI API."""
        if not self._client:
            raise EmbeddingError("OpenAI embed client not initialized")
        if not self._api_key:
            raise EmbeddingError("OPENAI_API_KEY not configured")

        start = time.monotonic()
        try:
            resp = await self._client.post(
                OPENAI_EMBED_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "input": texts,
                    "model": self._model,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise EmbeddingError(f"OpenAI HTTP {e.response.status_code}: {e.response.text}") from e
        except httpx.RequestError as e:
            raise EmbeddingError(f"OpenAI connection error: {e}") from e

        duration = int((time.monotonic() - start) * 1000)
        data = resp.json()

        embeddings = sorted(data.get("data", []), key=lambda x: x["index"])

        results = []
        for item in embeddings:
            results.append(EmbeddingResult(
                dense=item["embedding"],
                sparse=None,
                duration_ms=duration,
            ))

        log.info("openai_embed_complete", model=self._model, count=len(texts), duration_ms=duration)
        return results
