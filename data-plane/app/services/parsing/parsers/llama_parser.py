"""Document parser using LlamaParse (LlamaCloud API).

Primary parser for cloud deployments. Supports PDF, DOCX, DOC, PPTX, ODT,
and other complex document formats with high-quality extraction including
tables, images-to-text, and layout understanding.
"""

import asyncio
import os

import httpx

from app.config import ext
from app.services.parsing.models import DocumentMetadata, DocumentType, ParseOptions
from app.services.parsing.parsers.base import BaseParser, ParsedContent
from app.utils.logger import get_logger

log = get_logger(__name__)

LLAMA_CLOUD_BASE = ext.llama_cloud_base_url

# Polling settings
POLL_INTERVAL = 2  # seconds
MAX_POLL_ATTEMPTS = 150  # 5 minutes max


class LlamaParser(BaseParser):
    """Parse documents via the LlamaParse cloud API.

    Flow: upload file → poll for completion → fetch markdown result.
    """

    def __init__(self) -> None:
        self._api_key = ext.llama_cloud_api_key
        self._client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
        )

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()

    def supports(self) -> list[str]:
        return [
            DocumentType.PDF.value,
            DocumentType.DOCX.value,
            DocumentType.DOC.value,
            DocumentType.PPTX.value,
            DocumentType.ODT.value,
            DocumentType.JPG.value,
            DocumentType.PNG.value,
            DocumentType.GIF.value,
            DocumentType.BMP.value,
            DocumentType.WEBP.value,
            DocumentType.TIFF.value,
            DocumentType.SVG.value,
        ]

    async def check_health(self) -> bool:
        """Check if LlamaParse API is reachable."""
        if not self._api_key:
            return False
        try:
            client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10))
            resp = await client.get(
                f"{LLAMA_CLOUD_BASE}/supported-file-types",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def parse(self, file_path: str, options: ParseOptions) -> ParsedContent:
        if not self._api_key:
            raise RuntimeError("LLAMA_CLOUD_API_KEY is not configured")

        client = self._client
        if not client:
            raise RuntimeError("LlamaParser not started — call startup() first")

        # Step 1: Upload the file
        job_id = await self._upload(client, file_path, options)
        log.info("llama_parse_job_created", job_id=job_id, file=os.path.basename(file_path))

        # Step 2: Poll until complete
        await self._poll_until_done(client, job_id)

        # Step 3: Fetch the markdown result
        markdown = await self._fetch_result(client, job_id)

        # Step 4: Build ParsedContent
        word_count = len(markdown.split()) if markdown else 0
        metadata = DocumentMetadata(
            word_count=word_count,
            char_count=len(markdown),
        )

        return ParsedContent(
            text=markdown,
            metadata=metadata,
            tables=[],
            pages_parsed=1,
            pages_failed=0,
        )

    async def _upload(self, client: httpx.AsyncClient, file_path: str, options: ParseOptions) -> str:
        """Upload file to LlamaParse and return job ID."""
        filename = os.path.basename(file_path)

        with open(file_path, "rb") as f:
            files = {"file": (filename, f, "application/octet-stream")}
            data: dict[str, str] = {
                "result_type": "markdown",
            }
            if not options.extract_tables:
                data["skip_diagonal_text"] = "true"

            resp = await client.post(
                f"{LLAMA_CLOUD_BASE}/upload",
                files=files,
                data=data,
            )

        if resp.status_code != 200:
            raise RuntimeError(f"LlamaParse upload failed: HTTP {resp.status_code} — {resp.text}")

        result = resp.json()
        job_id = result.get("id")
        if not job_id:
            raise RuntimeError(f"LlamaParse upload returned no job ID: {result}")

        return job_id

    async def _poll_until_done(self, client: httpx.AsyncClient, job_id: str) -> None:
        """Poll the job status until it completes or fails."""
        for attempt in range(MAX_POLL_ATTEMPTS):
            resp = await client.get(f"{LLAMA_CLOUD_BASE}/job/{job_id}")
            if resp.status_code != 200:
                raise RuntimeError(f"LlamaParse status check failed: HTTP {resp.status_code}")

            status = resp.json().get("status", "")

            if status == "SUCCESS":
                log.info("llama_parse_complete", job_id=job_id, attempts=attempt + 1)
                return
            if status in ("ERROR", "FAILED"):
                error = resp.json().get("error", "Unknown error")
                raise RuntimeError(f"LlamaParse job failed: {error}")

            await asyncio.sleep(POLL_INTERVAL)

        raise RuntimeError(f"LlamaParse job timed out after {MAX_POLL_ATTEMPTS * POLL_INTERVAL}s")

    async def _fetch_result(self, client: httpx.AsyncClient, job_id: str) -> str:
        """Fetch the parsed markdown result."""
        resp = await client.get(f"{LLAMA_CLOUD_BASE}/job/{job_id}/result/markdown")
        if resp.status_code != 200:
            raise RuntimeError(f"LlamaParse result fetch failed: HTTP {resp.status_code}")

        result = resp.json()
        return result.get("markdown", "")
