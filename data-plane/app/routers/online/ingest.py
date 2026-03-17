"""
POST /api/v1/online/ingest — Ingest web-scraped content into the RAG pipeline.
"""

from fastapi import APIRouter, Request

from app.models.common import ErrorCode, ResponseEnvelope
from app.models.online.ingest import OnlineEntityCounts, OnlineIngestData, OnlineIngestRequest
from app.routers._ingest_utils import INGEST_ERROR_CODE_MAP
from app.services.ingest.ingest_service import IngestError
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/online", tags=["Online - Ingestion Pipeline"])


@router.post(
    "/ingest",
    summary="Ingest web content into the RAG pipeline",
    description=(
        "Takes web-scraped or URL-parsed text content and processes it through the full ingestion pipeline:\n\n"
        "1. **Chunk** — Split content using `fixed`, `sentence`, or `late_chunking` (default) strategy\n"
        "2. **Classify** — Categorize into one of 9 municipality content types + extract entities\n"
        "3. **Embed** — Generate dense vectors via OpenAI `text-embedding-3-small` (1536-dim)\n"
        "4. **Store** — Upsert vectors into the specified Qdrant `collection_name` with metadata\n\n"
        "**Vector modes** (via `vector_config.search_mode`):\n"
        "- `semantic` (default) — stores only dense cosine vectors (dimensionality controlled by `vector_config.vector_size`, default 1536). "
        "Best for pure semantic similarity search.\n"
        "- `hybrid` — stores both dense cosine vectors **and** sparse vectors. Enables combined semantic + lexical (BM25-style) search.\n\n"
        "The collection is **auto-created** if it does not exist, using the specified vector size and search mode.\n\n"
        "Previous vectors for the same `source_id` are deleted before upserting (idempotent).\n\n"
        "**Optional X-API-Key header** — required only when `DP_ONLINE_API_KEYS` is configured.\n\n"
        "**Error codes:** `VALIDATION_EMPTY_CONTENT`, `EMBEDDING_MODEL_NOT_LOADED`, "
        "`EMBEDDING_FAILED`, `EMBEDDING_OOM`, `QDRANT_CONNECTION_FAILED`, `QDRANT_COLLECTION_NOT_FOUND`, "
        "`QDRANT_UPSERT_FAILED`, `QDRANT_DISK_FULL`, `CLASSIFY_FAILED`"
    ),
    response_description="Ingestion result with chunk count, vector count, classification, and timing",
)
async def ingest_online(body: OnlineIngestRequest, request: Request) -> ResponseEnvelope[OnlineIngestData]:
    request_id = request.state.request_id
    ingest_svc = request.app.state.online_ingest

    if not body.content.strip():
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.VALIDATION_EMPTY_CONTENT,
            detail="Content must not be empty",
            request_id=request_id,
        )

    chunking = body.chunking
    vcfg = body.vector_config
    metadata_dict = body.metadata.model_dump()
    metadata_dict["source_url"] = body.url

    try:
        result = await ingest_svc.ingest(
            source_id=body.source_id,
            file_path=body.url,
            content=body.content,
            acl=None,
            metadata=metadata_dict,
            collection_name=body.collection_name,
            language=body.language,
            chunking_strategy=chunking.strategy if chunking else "late_chunking",
            max_chunk_size=chunking.max_chunk_size if chunking else None,
            chunk_overlap=chunking.overlap if chunking else None,
            vector_size=vcfg.vector_size if vcfg else 1536,
            search_mode=vcfg.search_mode.value if vcfg else "semantic",
        )
    except IngestError as e:
        error_code = INGEST_ERROR_CODE_MAP.get(e.code, ErrorCode.EMBEDDING_FAILED)
        log.error("ingest_online_failed", source_id=body.source_id, error=str(e), code=e.code)
        return ResponseEnvelope(
            success=False,
            error=error_code,
            detail=str(e),
            request_id=request_id,
        )

    return ResponseEnvelope(
        success=True,
        data=OnlineIngestData(
            source_id=result.source_id,
            chunks_created=result.chunks_created,
            vectors_stored=result.vectors_stored,
            collection=result.collection,
            content_type=result.classification,
            entities_extracted=OnlineEntityCounts(
                dates=result.entities_extracted.get("dates", 0),
                contacts=result.entities_extracted.get("contacts", 0),
                amounts=result.entities_extracted.get("amounts", 0),
            ),
            embedding_time_ms=result.embedding_time_ms,
            total_time_ms=result.total_time_ms,
        ),
        request_id=request_id,
    )
