"""
POST /api/v1/local/ingest — Ingest locally parsed content into the RAG pipeline.
"""

from fastapi import APIRouter, Request

from app.models.common import ErrorCode, ResponseEnvelope
from app.models.local.ingest import LocalEntityCounts, LocalIngestData, LocalIngestRequest
from app.routers._ingest_utils import INGEST_ERROR_CODE_MAP
from app.services.ingest.ingest_service import IngestError
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/local", tags=["Local - Ingestion Pipeline"])


@router.post(
    "/ingest",
    summary="Ingest a local document into the RAG pipeline",
    description=(
        "Takes locally parsed text content with ACL permissions and processes it through the full ingestion pipeline:\n\n"
        "1. **Chunk** — Split content using `fixed`, `sentence`, or `late_chunking` (default) strategy\n"
        "2. **Classify** — Categorize into one of 9 municipality content types + extract entities\n"
        "3. **Embed** — Generate dense (1024-dim) + sparse vectors via BGE-M3\n"
        "4. **Store** — Upsert vectors into the specified Qdrant `collection_name` with flattened ACL payload\n\n"
        "Previous vectors for the same `source_id` are deleted before upserting (idempotent).\n\n"
        "**Error codes:** `VALIDATION_EMPTY_CONTENT`, `VALIDATION_ACL_REQUIRED`, `EMBEDDING_MODEL_NOT_LOADED`, "
        "`EMBEDDING_FAILED`, `EMBEDDING_OOM`, `QDRANT_CONNECTION_FAILED`, `QDRANT_COLLECTION_NOT_FOUND`, "
        "`QDRANT_UPSERT_FAILED`, `QDRANT_DISK_FULL`, `CLASSIFY_FAILED`"
    ),
    response_description="Ingestion result with chunk count, vector count, classification, and timing",
)
async def ingest_local(body: LocalIngestRequest, request: Request) -> ResponseEnvelope[LocalIngestData]:
    request_id = request.state.request_id
    ingest_svc = request.app.state.ingest

    if not body.content.strip():
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.VALIDATION_EMPTY_CONTENT,
            detail="Content must not be empty",
            request_id=request_id,
        )

    chunking = body.chunking
    acl_dict = body.acl.model_dump()
    metadata_dict = body.metadata.model_dump()

    try:
        result = await ingest_svc.ingest(
            source_id=body.source_id,
            file_path=body.file_path,
            content=body.content,
            acl=acl_dict,
            metadata=metadata_dict,
            collection_name=body.collection_name,
            language=body.language,
            chunking_strategy=chunking.strategy if chunking else "late_chunking",
            max_chunk_size=chunking.max_chunk_size if chunking else None,
            chunk_overlap=chunking.overlap if chunking else None,
        )
    except IngestError as e:
        error_code = INGEST_ERROR_CODE_MAP.get(e.code, ErrorCode.EMBEDDING_FAILED)
        log.error("ingest_local_failed", source_id=body.source_id, error=str(e), code=e.code)
        return ResponseEnvelope(
            success=False,
            error=error_code,
            detail=str(e),
            request_id=request_id,
        )

    return ResponseEnvelope(
        success=True,
        data=LocalIngestData(
            source_id=result.source_id,
            chunks_created=result.chunks_created,
            vectors_stored=result.vectors_stored,
            collection=result.collection,
            content_type=result.classification,
            entities_extracted=LocalEntityCounts(
                dates=result.entities_extracted.get("dates", 0),
                contacts=result.entities_extracted.get("contacts", 0),
                amounts=result.entities_extracted.get("amounts", 0),
            ),
            embedding_time_ms=result.embedding_time_ms,
            total_time_ms=result.total_time_ms,
        ),
        request_id=request_id,
    )
