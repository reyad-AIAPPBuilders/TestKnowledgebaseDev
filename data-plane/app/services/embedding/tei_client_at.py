"""TEI embedding client for the AT funding-assistant ingest pipeline.

Talks to a self-hosted Text Embeddings Inference server exposing an
OpenAI-compatible ``POST /v1/embeddings`` endpoint. Bearer auth is required.

Used only by ``POST /api/v1/online/ingest/at`` — the AT funding collection is
pre-created with a 1024-dim vector, so the TEI model behind ``TEI_EMBED_URL_AT``
must produce 1024-dim outputs.
"""

import time

import httpx

from app.config import ext
from app.services.embedding.bge_m3_client import EmbeddingError, EmbeddingResult
from app.utils.logger import get_logger

log = get_logger(__name__)


class TEIEmbedClientAT:
    """HTTP client for the AT TEI embedding server (OpenAI-compatible)."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._base_url = ext.tei_embed_url_at.rstrip("/")
        self._api_key = ext.tei_embed_api_key_at
        self._model = ext.tei_embed_model_at

    async def startup(self) -> None:
        if not self._api_key:
            log.warning("tei_embed_client_at_no_key")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60))
        log.info("tei_embed_client_at_started", url=self._base_url, model=self._model or "<server-default>")

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
        log.info("tei_embed_client_at_stopped")

    async def check_health(self) -> bool:
        return bool(self._client and self._api_key)

    async def embed(self, text: str) -> EmbeddingResult:
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        if not self._client:
            raise EmbeddingError("TEI AT embed client not initialized")
        if not self._api_key:
            raise EmbeddingError("TEI_EMBED_API_KEY_AT not configured")

        payload: dict = {"input": texts}
        if self._model:
            payload["model"] = self._model

        start = time.monotonic()
        try:
            resp = await self._client.post(
                f"{self._base_url}/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise EmbeddingError(f"TEI AT HTTP {e.response.status_code}: {e.response.text}") from e
        except httpx.RequestError as e:
            raise EmbeddingError(f"TEI AT connection error: {e}") from e

        duration = int((time.monotonic() - start) * 1000)
        data = resp.json()
        items = sorted(data.get("data", []), key=lambda x: x["index"])

        results = [
            EmbeddingResult(dense=item["embedding"], sparse=None, duration_ms=duration)
            for item in items
        ]
        log.info("tei_embed_at_complete", count=len(texts), duration_ms=duration)
        return results
