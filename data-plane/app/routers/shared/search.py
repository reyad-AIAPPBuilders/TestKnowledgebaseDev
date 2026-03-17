"""
POST /api/v1/search — Semantic search with mandatory permission filtering.
"""

from fastapi import APIRouter, Request

from app.models.common import ErrorCode, ResponseEnvelope
from app.models.search import (
    PermissionFilterApplied,
    SearchData,
    SearchRequest,
    SearchResult,
    SearchResultEntities,
    SearchResultMetadata,
)
from app.services.search.search_service import SearchError
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Semantic Search"])

_ERROR_CODE_MAP = {
    "VALIDATION_USER_REQUIRED": ErrorCode.VALIDATION_USER_REQUIRED,
    "EMBEDDING_MODEL_NOT_LOADED": ErrorCode.EMBEDDING_MODEL_NOT_LOADED,
    "EMBEDDING_FAILED": ErrorCode.EMBEDDING_FAILED,
    "QDRANT_CONNECTION_FAILED": ErrorCode.QDRANT_CONNECTION_FAILED,
    "QDRANT_COLLECTION_NOT_FOUND": ErrorCode.QDRANT_COLLECTION_NOT_FOUND,
    "QDRANT_SEARCH_FAILED": ErrorCode.QDRANT_SEARCH_FAILED,
}


@router.post(
    "/search",
    summary="Permission-aware semantic search",
    description=(
        "Search a specific Qdrant `collection_name` with **mandatory permission filtering** based on user identity.\n\n"
        "**Multi-tenant:** The caller specifies which `collection_name` to search in.\n\n"
        "**Permission model:**\n"
        "- `citizen` — Can only see documents with `visibility: public`\n"
        "- `employee` — Can see `public` + `internal` documents filtered by AD group membership\n\n"
        "**The search pipeline:**\n"
        "1. Embed the query via BGE-M3 (1024-dim dense vector)\n"
        "2. Build Qdrant filter from user permissions (visibility + group intersection)\n"
        "3. Execute nearest-neighbor search with score threshold\n"
        "4. Return ranked results with transparency on which filters were applied\n\n"
        "**Result metadata** includes `organization_id`, `department`, `title`, `source_type`, "
        "extracted entities (amounts, deadlines), and content classification.\n\n"
        "**Optional filters:** `classification` (e.g. `funding`, `event`, `policy`)\n\n"
        "**Error codes:** `VALIDATION_USER_REQUIRED`, `EMBEDDING_MODEL_NOT_LOADED`, `EMBEDDING_FAILED`, "
        "`QDRANT_CONNECTION_FAILED`, `QDRANT_COLLECTION_NOT_FOUND`, `QDRANT_SEARCH_FAILED`"
    ),
    response_description="Ranked search results with scores, metadata, and permission filter transparency",
)
async def search(body: SearchRequest, request: Request) -> ResponseEnvelope[SearchData]:
    request_id = request.state.request_id
    search_svc = request.app.state.search

    classification_filter = None
    if body.filters and body.filters.classification:
        classification_filter = body.filters.classification

    try:
        result = await search_svc.search(
            query=body.query,
            collection_name=body.collection_name,
            user_type=body.user.type,
            user_id=body.user.user_id,
            user_groups=body.user.groups,
            user_roles=body.user.roles,
            user_department=body.user.department,
            classification_filter=classification_filter,
            top_k=body.top_k,
            score_threshold=body.score_threshold,
        )
    except SearchError as e:
        error_code = _ERROR_CODE_MAP.get(e.code, ErrorCode.QDRANT_SEARCH_FAILED)
        log.error("search_failed", error=str(e), code=e.code)
        return ResponseEnvelope(
            success=False,
            error=error_code,
            detail=str(e),
            request_id=request_id,
        )

    search_results = [
        SearchResult(
            chunk_id=r.chunk_id,
            source_id=r.source_id,
            chunk_text=r.chunk_text,
            score=r.score,
            source_path=r.source_path,
            classification=r.classification,
            entities=SearchResultEntities(
                amounts=r.entity_amounts,
                deadlines=r.entity_deadlines,
            ),
            metadata=SearchResultMetadata(
                title=r.title,
                organization_id=r.organization_id,
                department=r.department,
                source_type=r.source_type,
            ),
        )
        for r in result.results
    ]

    return ResponseEnvelope(
        success=True,
        data=SearchData(
            results=search_results,
            total_results=result.total_results,
            query_embedding_ms=result.query_embedding_ms,
            search_ms=result.search_ms,
            permission_filter_applied=PermissionFilterApplied(
                visibility=result.permission_filter.visibility,
                must_match_groups=result.permission_filter.must_match_groups,
                must_not_match_groups=result.permission_filter.must_not_match_groups,
            ),
        ),
        request_id=request_id,
    )
