"""Contextual Retrieval — generates short context for each chunk using OpenAI.

Based on Anthropic's Contextual Retrieval technique: prepends a concise context
to each chunk explaining how it fits within the whole document, improving
retrieval accuracy.

The context is generated in the same language as the content.
"""

import asyncio
import time

import httpx

from app.config import ext
from app.utils.logger import get_logger

log = get_logger(__name__)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

CONTEXT_PROMPT = """\
<document>
{document}
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
{chunk}
</chunk>

Give a short succinct context (2-3 sentences) to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. Respond ONLY with the context, nothing else. Write the context in the same language as the content."""


class ContextualEnricher:
    """Enriches chunks with document-level context via OpenAI."""

    def __init__(self, model: str | None = None, max_concurrent: int = 5) -> None:
        self._client: httpx.AsyncClient | None = None
        self._model = model or ext.openai_model
        self._api_key = ext.openai_api_key
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def startup(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60))
        log.info("contextual_enricher_started", model=self._model)

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
        log.info("contextual_enricher_stopped")

    async def enrich_chunks(
        self,
        document: str,
        chunks: list[str],
    ) -> list[str]:
        """Prepend contextual descriptions to each chunk.

        Returns a list of enriched chunks: "{context}\n\n{original_chunk}"
        """
        start = time.monotonic()

        # Truncate document to ~6000 chars for the prompt to stay within token limits
        doc_summary = document[:6000]

        tasks = [
            self._enrich_single(doc_summary, chunk)
            for chunk in chunks
        ]
        enriched = await asyncio.gather(*tasks)

        duration = int((time.monotonic() - start) * 1000)
        log.info(
            "contextual_enrichment_complete",
            chunks=len(chunks),
            duration_ms=duration,
        )
        return enriched

    async def _enrich_single(self, document: str, chunk: str) -> str:
        """Generate context for a single chunk and prepend it."""
        if not self._api_key or not self._client:
            return chunk

        async with self._semaphore:
            try:
                resp = await self._client.post(
                    OPENAI_CHAT_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [
                            {
                                "role": "user",
                                "content": CONTEXT_PROMPT.format(
                                    document=document,
                                    chunk=chunk,
                                ),
                            }
                        ],
                        "max_tokens": 200,
                        "temperature": 0.0,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                context = data["choices"][0]["message"]["content"].strip()
                return f"{context}\n\n{chunk}"
            except Exception as e:
                log.warning("contextual_enrichment_failed", error=str(e))
                return chunk
