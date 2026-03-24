"""
GET /api/v1/online/available_collections — List all Qdrant collections with info
"""

from fastapi import APIRouter, Request

from app.models.collections import AvailableCollectionsData, CollectionInfo
from app.models.common import ErrorCode, ResponseEnvelope
from app.services.embedding.qdrant_service import QdrantError
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/online", tags=["Online - Collection Management"])


@router.get(
    "/available_collections",
    summary="List available Qdrant collections",
    description=(
        "Returns a list of all Qdrant collections with basic information including "
        "vector count, point count, segments, disk usage, and status.\n\n"
        "**Error codes:** `QDRANT_CONNECTION_FAILED`"
    ),
    response_description="List of available collections with info",
)
async def available_collections(
    request: Request,
) -> ResponseEnvelope[AvailableCollectionsData]:
    request_id = request.state.request_id
    qdrant = request.app.state.qdrant

    try:
        collections_raw = await qdrant.list_collections()
    except QdrantError as e:
        log.error("list_collections_failed", error=str(e))
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.QDRANT_CONNECTION_FAILED,
            detail=str(e),
            request_id=request_id,
        )

    collections = [CollectionInfo(**col) for col in collections_raw]

    return ResponseEnvelope(
        success=True,
        data=AvailableCollectionsData(
            total=len(collections),
            collections=collections,
        ),
        request_id=request_id,
    )
