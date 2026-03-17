"""
POST /api/v1/collections/init  — Create a Qdrant collection for a municipality
GET  /api/v1/collections/stats — Get collection statistics
"""

from fastapi import APIRouter, Query, Request
from app.models.collections import (
    CollectionStatsData,
    InitCollectionData,
    InitCollectionRequest,
    VectorConfig,
)
from app.models.common import ErrorCode, ResponseEnvelope
from app.services.embedding.qdrant_service import QdrantError
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Collection Management"])


@router.post(
    "/collections/init",
    summary="Initialize a Qdrant collection",
    description="Create a new Qdrant vector collection for a municipality tenant. Supports dense vectors (default 1024-dim for BGE-M3) and optional sparse vectors for hybrid search.\n\nIf the collection already exists, returns `created: false` without error.\n\n**Error codes:** `QDRANT_CONNECTION_FAILED`",
    response_description="Collection creation result with vector configuration",
)
async def init_collection(
    body: InitCollectionRequest, request: Request
) -> ResponseEnvelope[InitCollectionData]:
    request_id = request.state.request_id
    qdrant = request.app.state.qdrant

    config = body.vector_config or VectorConfig()

    try:
        created = await qdrant.create_collection(
            name=body.collection_name,
            dense_dim=config.dense_dim,
            sparse=config.sparse,
            distance=config.distance.capitalize(),
        )
    except QdrantError as e:
        log.error("collection_init_failed", collection=body.collection_name, error=str(e))
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.QDRANT_CONNECTION_FAILED,
            detail=str(e),
            request_id=request_id,
        )

    return ResponseEnvelope(
        success=True,
        data=InitCollectionData(
            collection=body.collection_name,
            created=created,
            dense_dim=config.dense_dim,
            sparse_enabled=config.sparse,
        ),
        request_id=request_id,
    )


@router.get(
    "/collections/stats",
    summary="Get collection statistics",
    description=(
        "Returns statistics for the specified Qdrant collection, including total vectors, disk usage, and segment count.\n\n"
        "**Required query parameter:** `collection_name` — the Qdrant collection to inspect.\n\n"
        "**Error codes:** `QDRANT_COLLECTION_NOT_FOUND`, `QDRANT_CONNECTION_FAILED`"
    ),
    response_description="Collection statistics with vector count and disk usage",
)
async def collection_stats(
    request: Request,
    collection_name: str = Query(..., description="Qdrant collection name to get stats for"),
) -> ResponseEnvelope[CollectionStatsData]:
    request_id = request.state.request_id
    qdrant = request.app.state.qdrant
    collection = collection_name

    if not collection:
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.QDRANT_COLLECTION_NOT_FOUND,
            detail="collection_name query parameter is required",
            request_id=request_id,
        )

    try:
        stats = await qdrant.collection_stats(collection)
    except QdrantError as e:
        error_msg = str(e).lower()
        error_code = (
            ErrorCode.QDRANT_COLLECTION_NOT_FOUND
            if "not found" in error_msg
            else ErrorCode.QDRANT_CONNECTION_FAILED
        )
        log.error("collection_stats_failed", collection=collection, error=str(e))
        return ResponseEnvelope(
            success=False,
            error=error_code,
            detail=str(e),
            request_id=request_id,
        )

    # Extract stats from Qdrant response
    total_vectors = stats.get("vectors_count", stats.get("points_count", 0))
    segments_count = stats.get("segments_count", 0)
    # Disk usage from indexed size if available
    disk_bytes = stats.get("disk_data_size", 0)
    disk_mb = round(disk_bytes / (1024 * 1024), 1) if disk_bytes else 0.0

    return ResponseEnvelope(
        success=True,
        data=CollectionStatsData(
            collection=collection,
            total_vectors=total_vectors,
            total_documents=0,  # Requires scroll/aggregation — filled by ingest pipeline
            disk_usage_mb=disk_mb,
            by_classification={},  # Requires faceted aggregation — filled by ingest pipeline
            by_visibility={},  # Requires faceted aggregation — filled by ingest pipeline
        ),
        request_id=request_id,
    )
