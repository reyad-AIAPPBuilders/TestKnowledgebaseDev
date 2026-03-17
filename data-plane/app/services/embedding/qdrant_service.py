"""Qdrant vector database client — manages collections, upserts, searches, and deletions."""

import time

import httpx

from app.config import ext
from app.utils.logger import get_logger

log = get_logger(__name__)


class QdrantError(Exception):
    pass


class QdrantService:
    """HTTP client for the Qdrant vector database."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._base_url = ext.qdrant_url.rstrip("/")

    async def startup(self) -> None:
        headers = {}
        if ext.qdrant_api_key:
            headers["api-key"] = ext.qdrant_api_key
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(30),
            headers=headers,
        )
        log.info("qdrant_service_started", url=self._base_url)

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
        log.info("qdrant_service_stopped")

    async def check_health(self) -> bool:
        if not self._client:
            return False
        try:
            resp = await self._client.get("/healthz", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    # ── Collection management ────────────────────────────────────────

    async def create_collection(
        self,
        name: str,
        dense_dim: int = 1024,
        sparse: bool = True,
        distance: str = "Cosine",
    ) -> bool:
        """Create a Qdrant collection. Returns True if created, False if already exists."""
        if not self._client:
            raise QdrantError("Qdrant client not initialized")

        # Check if collection exists
        try:
            resp = await self._client.get(f"/collections/{name}")
            if resp.status_code == 200:
                log.info("qdrant_collection_exists", collection=name)
                return False
        except httpx.RequestError as e:
            raise QdrantError(f"Qdrant connection failed: {e}") from e

        # Build vectors config
        vectors_config = {
            "dense": {
                "size": dense_dim,
                "distance": distance,
            },
        }

        if sparse:
            sparse_config = {
                "sparse": {
                    "index": {"on_disk": False},
                },
            }
        else:
            sparse_config = {}

        body: dict = {"vectors": vectors_config}
        if sparse_config:
            body["sparse_vectors"] = sparse_config

        try:
            resp = await self._client.put(
                f"/collections/{name}",
                json=body,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise QdrantError(f"Failed to create collection: {e.response.text}") from e
        except httpx.RequestError as e:
            raise QdrantError(f"Qdrant connection failed: {e}") from e

        log.info("qdrant_collection_created", collection=name, dense_dim=dense_dim, sparse=sparse)
        return True

    async def collection_stats(self, name: str) -> dict:
        """Get collection info and stats."""
        if not self._client:
            raise QdrantError("Qdrant client not initialized")

        try:
            resp = await self._client.get(f"/collections/{name}")
            if resp.status_code == 404:
                raise QdrantError(f"Collection not found: {name}")
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise QdrantError(f"Collection not found: {name}") from e
            raise QdrantError(f"Qdrant error: {e.response.text}") from e
        except httpx.RequestError as e:
            raise QdrantError(f"Qdrant connection failed: {e}") from e

        return resp.json().get("result", {})

    # ── Vector operations ────────────────────────────────────────────

    async def upsert_points(
        self,
        collection: str,
        points: list[dict],
    ) -> int:
        """Upsert points into a collection. Returns count of upserted points."""
        if not self._client:
            raise QdrantError("Qdrant client not initialized")

        try:
            resp = await self._client.put(
                f"/collections/{collection}/points",
                json={"points": points},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise QdrantError(f"Upsert failed: {e.response.text}") from e
        except httpx.RequestError as e:
            raise QdrantError(f"Qdrant connection failed: {e}") from e

        log.info("qdrant_upsert", collection=collection, count=len(points))
        return len(points)

    async def delete_by_source_id(self, collection: str, source_id: str) -> int:
        """Delete all points for a source_id. Returns estimated count deleted."""
        if not self._client:
            raise QdrantError("Qdrant client not initialized")

        # First count matching points
        count = await self._count_by_source_id(collection, source_id)

        try:
            resp = await self._client.post(
                f"/collections/{collection}/points/delete",
                json={
                    "filter": {
                        "must": [
                            {"key": "source_id", "match": {"value": source_id}},
                        ],
                    },
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise QdrantError(f"Delete failed: {e.response.text}") from e
        except httpx.RequestError as e:
            raise QdrantError(f"Qdrant connection failed: {e}") from e

        log.info("qdrant_delete", collection=collection, source_id=source_id, deleted=count)
        return count

    async def delete_by_filter(
        self,
        collection: str,
        qdrant_filter: dict,
    ) -> int:
        """Delete points matching an arbitrary Qdrant filter. Returns estimated count deleted."""
        if not self._client:
            raise QdrantError("Qdrant client not initialized")

        # Count before deleting
        count = 0
        try:
            resp = await self._client.post(
                f"/collections/{collection}/points/count",
                json={"filter": qdrant_filter, "exact": True},
            )
            resp.raise_for_status()
            count = resp.json().get("result", {}).get("count", 0)
        except Exception:
            pass

        try:
            resp = await self._client.post(
                f"/collections/{collection}/points/delete",
                json={"filter": qdrant_filter},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise QdrantError(f"Delete failed: {e.response.text}") from e
        except httpx.RequestError as e:
            raise QdrantError(f"Qdrant connection failed: {e}") from e

        log.info("qdrant_delete_by_filter", collection=collection, deleted=count)
        return count

    async def update_payload(
        self,
        collection: str,
        source_id: str,
        payload: dict,
    ) -> int:
        """Update payload fields on all points matching source_id."""
        if not self._client:
            raise QdrantError("Qdrant client not initialized")

        count = await self._count_by_source_id(collection, source_id)

        try:
            resp = await self._client.post(
                f"/collections/{collection}/points/payload",
                json={
                    "payload": payload,
                    "filter": {
                        "must": [
                            {"key": "source_id", "match": {"value": source_id}},
                        ],
                    },
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise QdrantError(f"Payload update failed: {e.response.text}") from e
        except httpx.RequestError as e:
            raise QdrantError(f"Qdrant connection failed: {e}") from e

        log.info("qdrant_payload_update", collection=collection, source_id=source_id, updated=count)
        return count

    async def search(
        self,
        collection: str,
        dense_vector: list[float],
        sparse_vector: dict[int, float] | None = None,
        filters: dict | None = None,
        top_k: int = 10,
        score_threshold: float = 0.0,
    ) -> list[dict]:
        """Search for similar vectors with optional filtering."""
        if not self._client:
            raise QdrantError("Qdrant client not initialized")

        start = time.monotonic()
        body: dict = {
            "vector": {"name": "dense", "vector": dense_vector},
            "limit": top_k,
            "with_payload": True,
            "score_threshold": score_threshold,
        }

        if filters:
            body["filter"] = filters

        try:
            resp = await self._client.post(
                f"/collections/{collection}/points/search",
                json=body,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise QdrantError(f"Search failed: {e.response.text}") from e
        except httpx.RequestError as e:
            raise QdrantError(f"Qdrant connection failed: {e}") from e

        duration = int((time.monotonic() - start) * 1000)
        results = resp.json().get("result", [])
        log.info("qdrant_search", collection=collection, results=len(results), duration_ms=duration)
        return results

    async def _count_by_source_id(self, collection: str, source_id: str) -> int:
        """Count points matching a source_id."""
        try:
            resp = await self._client.post(
                f"/collections/{collection}/points/count",
                json={
                    "filter": {
                        "must": [
                            {"key": "source_id", "match": {"value": source_id}},
                        ],
                    },
                    "exact": True,
                },
            )
            resp.raise_for_status()
            return resp.json().get("result", {}).get("count", 0)
        except Exception:
            return 0
