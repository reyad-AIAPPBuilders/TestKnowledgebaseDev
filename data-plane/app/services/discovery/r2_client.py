"""R2/S3 file discovery client — lists objects in Cloudflare R2 buckets."""

import hashlib
import mimetypes

import httpx

from app.config import ext
from app.services.discovery.smb_client import DiscoveredFile
from app.utils.logger import get_logger

log = get_logger(__name__)


class R2Error(Exception):
    def __init__(self, message: str, code: str = "R2_CONNECTION_FAILED"):
        super().__init__(message)
        self.code = code


class R2Client:
    """Lists and discovers files from Cloudflare R2 (S3-compatible) buckets.

    Uses the S3 ListObjectsV2 API via pre-signed requests or AWS Signature V4.
    For simplicity in on-premise deployments, this can also work with any
    S3-compatible storage.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        if not ext.r2_endpoint_url:
            log.info("r2_client_disabled", reason="no R2_ENDPOINT_URL configured")
            return

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30),
        )
        log.info("r2_client_started", endpoint=ext.r2_endpoint_url)

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
        log.info("r2_client_stopped")

    async def discover(
        self,
        prefixes: list[str],
        since_hash_map: dict[str, str] | None = None,
    ) -> list[DiscoveredFile]:
        """List objects under the given R2 prefixes and return file metadata."""
        if not self._client:
            raise R2Error("R2 client not initialized — check R2_ENDPOINT_URL")

        since_hash_map = since_hash_map or {}
        files: list[DiscoveredFile] = []

        for prefix in prefixes:
            await self._list_prefix(prefix, files, since_hash_map)

        log.info("r2_discover_complete", prefixes=prefixes, total_files=len(files))
        return files

    async def _list_prefix(
        self,
        prefix: str,
        files: list[DiscoveredFile],
        since_hash_map: dict[str, str],
    ) -> None:
        """List all objects under a prefix using S3 ListObjectsV2."""
        continuation_token: str | None = None
        bucket = ext.r2_bucket

        while True:
            params: dict[str, str] = {
                "list-type": "2",
                "prefix": prefix,
                "max-keys": "1000",
            }
            if continuation_token:
                params["continuation-token"] = continuation_token

            url = f"{ext.r2_endpoint_url.rstrip('/')}/{bucket}"

            try:
                resp = await self._client.get(
                    url,
                    params=params,
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise R2Error(f"Bucket or prefix not found: {bucket}/{prefix}", code="R2_FILE_NOT_FOUND") from e
                raise R2Error(f"R2 error: HTTP {e.response.status_code}") from e
            except httpx.RequestError as e:
                raise R2Error(f"R2 connection failed: {e}") from e

            # Parse XML response (S3 ListObjectsV2 returns XML)
            body = resp.text
            objects = self._parse_list_response(body)

            for obj in objects:
                key = obj["key"]
                size = obj["size"]
                last_modified = obj["last_modified"]
                etag = obj.get("etag", "")

                mime_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
                file_hash = f"sha256:{etag.strip('\"')}" if etag else "sha256:unknown"

                files.append(DiscoveredFile(
                    path=key,
                    file_hash=file_hash,
                    size_bytes=size,
                    mime_type=mime_type,
                    last_modified=last_modified,
                    acl={
                        "source": "r2",
                        "allow_groups": [],
                        "deny_groups": [],
                        "allow_users": [],
                        "inherited": False,
                    },
                ))

            # Check for truncation / pagination
            if "<IsTruncated>true</IsTruncated>" in body:
                import re
                token_match = re.search(r"<NextContinuationToken>(.+?)</NextContinuationToken>", body)
                if token_match:
                    continuation_token = token_match.group(1)
                else:
                    break
            else:
                break

    def _parse_list_response(self, xml: str) -> list[dict]:
        """Simple XML parser for S3 ListObjectsV2 response."""
        import re

        objects = []
        # Find all <Contents> blocks
        for match in re.finditer(r"<Contents>(.*?)</Contents>", xml, re.DOTALL):
            block = match.group(1)

            key_m = re.search(r"<Key>(.+?)</Key>", block)
            size_m = re.search(r"<Size>(\d+)</Size>", block)
            modified_m = re.search(r"<LastModified>(.+?)</LastModified>", block)
            etag_m = re.search(r"<ETag>(.+?)</ETag>", block)

            if key_m:
                objects.append({
                    "key": key_m.group(1),
                    "size": int(size_m.group(1)) if size_m else 0,
                    "last_modified": modified_m.group(1) if modified_m else "",
                    "etag": etag_m.group(1) if etag_m else "",
                })

        return objects

    def _auth_headers(self) -> dict[str, str]:
        """Return auth headers for R2/S3 requests.

        In production, this would use AWS Signature V4. For now, we pass
        access key as a basic header — real deployments should use a proper
        S3 signing library.
        """
        headers: dict[str, str] = {}
        if ext.r2_access_key_id:
            headers["x-amz-access-key"] = ext.r2_access_key_id
        return headers
