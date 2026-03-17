"""HTTP document downloader with streaming, size limits, and proper error handling."""

import os
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from app.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)

TEMP_DIR = os.environ.get("DP_TEMP_DIR", "/tmp/dp_downloads")


class DownloadError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class DownloadResult:
    def __init__(
        self,
        file_path: str,
        content_type: str | None,
        filename: str | None,
        file_size: int,
    ):
        self.file_path = file_path
        self.content_type = content_type
        self.filename = filename
        self.file_size = file_size


def _extract_filename(url: str, headers: httpx.Headers) -> str | None:
    cd = headers.get("content-disposition")
    if cd:
        parts = cd.split("filename=")
        if len(parts) > 1:
            name = parts[1].strip().strip('"').strip("'")
            if name:
                return name

    parsed = urlparse(url)
    path = unquote(parsed.path)
    if path and "/" in path:
        name = path.rsplit("/", 1)[-1]
        if "." in name:
            return name

    return None


async def download_document(
    url: str,
    client: httpx.AsyncClient | None = None,
) -> DownloadResult:
    """Download a document from URL to a temp file with streaming and size limits."""
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(60),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; KI2-DataPlane/1.0)"},
        )

    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        log.info("download_start", url=url)

        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise DownloadError(
                    f"HTTP {response.status_code}: {response.reason_phrase}",
                    status_code=response.status_code,
                )

            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise DownloadError(f"File too large: {int(content_length)} bytes (max {max_bytes})")

            content_type = response.headers.get("content-type")
            filename = _extract_filename(url, response.headers)

            suffix = Path(filename).suffix if filename else ""
            fd, temp_path = tempfile.mkstemp(suffix=suffix, dir=TEMP_DIR)

            total_size = 0
            try:
                with os.fdopen(fd, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        total_size += len(chunk)
                        if total_size > max_bytes:
                            raise DownloadError(f"File too large: exceeded {max_bytes} bytes")
                        f.write(chunk)
            except Exception:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise

            log.info("download_complete", url=url, size=total_size, content_type=content_type, filename=filename)

            return DownloadResult(
                file_path=temp_path,
                content_type=content_type,
                filename=filename,
                file_size=total_size,
            )
    finally:
        if should_close:
            await client.aclose()


def cleanup_file(path: str) -> None:
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError as e:
        log.warning("cleanup_failed", path=path, error=str(e))
