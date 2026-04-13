"""
POST /api/v1/online/ingest — Ingest web-scraped content into the RAG pipeline.
"""

from fastapi import APIRouter, Request

from app.config import ext
from app.models.common import ErrorCode, ResponseEnvelope
from app.models.online.ingest import OnlineIngestData, OnlineIngestRequest
from app.routers._ingest_utils import INGEST_ERROR_CODE_MAP
from app.services.ingest.ingest_service import IngestError
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/online", tags=["Online - Ingestion Pipeline"])


@router.post(
    "/ingest",
    summary="Ingest web content into the RAG pipeline",
    description=(
        "Takes web-scraped or URL-parsed text content and processes it through the ingestion pipeline:\n\n"
        "1. **Chunk** — Split content using `contextual` (default), `late_chunking`, `sentence`, or `fixed` strategy\n"
        "2. **Contextual Enrichment** — (when using `contextual` strategy) Prepend AI-generated context to each chunk via OpenAI, improving retrieval accuracy\n"
        "3. **Embed** — Generate **multi-vector** dense embeddings via OpenAI `text-embedding-3-small` (1536-dim) "
        "**and** BGE-multilingual-gemma2 via LiteLLM (fallback, configurable dim). "
        "If one embedder fails, the point is still stored with the other's vector.\n"
        "4. **Store** — Upsert vectors into the specified Qdrant `collection_name` with metadata\n\n"
        "**Content type is supplied by the caller.** The `content_type` field is **required** — "
        "obtain it upfront from `/online/scrape` or `/online/document-parse`, which now run the classifier "
        "and return `content_type` on their responses. Classification is no longer performed inside this endpoint. "
        "Content-type gating (e.g. skipping non-funding content when `assistant_type` is `\"funding\"`) "
        "is expected to be done by the caller before invoking ingest.\n\n"
        "**Multi-vector architecture:**\n"
        "Every point stores two dense vectors: `dense_openai` (primary) and `dense_bge_gemma2` (fallback). "
        "During search, OpenAI is tried first; if it is unavailable, `dense_bge_gemma2` is used automatically.\n\n"
        "**Vector modes** (via `vector_config.search_mode`):\n"
        "- `semantic` (default) — stores `dense_openai` + `dense_bge_gemma2` cosine vectors. "
        "Best for pure semantic similarity search.\n"
        "- `hybrid` — stores `dense_openai` + `dense_bge_gemma2` + `sparse` (BM25) vectors. "
        "Enables combined semantic + lexical search.\n\n"
        "The collection is **auto-created** if it does not exist, using the specified vector size and search mode.\n\n"
        "**Funding metadata extraction:** When `assistant_type` is `\"funding\"`, "
        "an additional OpenAI call extracts structured funding metadata (`country_code`, `state_or_province`, `city`, "
        "`target_group`, `funding_type`, `status`, `funding_amount`, `thematic_focus`, `eligibility_criteria`, "
        "`legal_basis`, `funding_provider`, `reference_number`, `start_date`, `end_date`, `scraped_at`). "
        "These fields are merged flat into each Qdrant point's metadata for filtering.\n\n"
        "**Country constraint:** The `country` field (ISO 3166-1 alpha-2) is **required** when `assistant_type` is `\"funding\"`. "
        "It constrains `state_or_province` to the official administrative divisions for that country "
        "(supported: AT, DE, CH, RO, IT, FR, HU, CZ, SK, SI, HR). Values not in the known list are dropped.\n\n"
        "Previous vectors for the same `source_id` are deleted before upserting (idempotent).\n\n"
        "**Optional X-API-Key header** — required only when `DP_ONLINE_API_KEYS` is configured.\n\n"
        "**Error codes:** `VALIDATION_EMPTY_CONTENT`, `EMBEDDING_MODEL_NOT_LOADED`, "
        "`EMBEDDING_FAILED`, `EMBEDDING_OOM`, `QDRANT_CONNECTION_FAILED`, `QDRANT_COLLECTION_NOT_FOUND`, "
        "`QDRANT_UPSERT_FAILED`, `QDRANT_DISK_FULL`"
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

    # ── Funding metadata extraction (only for funding assistant) ──
    funding_metadata: dict | None = None
    if body.assistant_type == "funding":
        extractor = request.app.state.funding_extractor
        try:
            funding_metadata = await extractor.extract(body.content, source_url=body.url, country=body.country)
        except Exception as e:
            log.warning("ingest_online_funding_extract_failed", source_id=body.source_id, error=str(e))
            # Non-fatal: proceed with ingestion even if extraction fails

    chunking = body.chunking
    vcfg = body.vector_config
    # Build metadata: extracted funding fields first, then request body overwrites duplicates
    request_meta = body.metadata.model_dump()
    if funding_metadata:
        metadata_dict = {**funding_metadata, **request_meta}
    else:
        metadata_dict = request_meta
    metadata_dict["source_url"] = body.url
    metadata_dict["assistant_type"] = body.assistant_type

    # Explicit state_or_province override from request body: stored verbatim, bypassing extractor normalization.
    if body.state_or_province:
        metadata_dict["state_or_province"] = body.state_or_province

    try:
        result = await ingest_svc.ingest(
            source_id=body.source_id,
            file_path=body.url,
            content=body.content,
            acl=None,
            metadata=metadata_dict,
            collection_name=body.collection_name,
            language=body.language,
            chunking_strategy=chunking.strategy if chunking else "contextual",
            max_chunk_size=chunking.max_chunk_size if chunking else None,
            chunk_overlap=chunking.overlap if chunking else None,
            vector_size=vcfg.vector_size if vcfg else 1536,
            search_mode=vcfg.search_mode.value if vcfg else "semantic",
            fallback_dense_dim=ext.bge_gemma2_dense_dim if (vcfg and vcfg.enable_fallback) else None,
            content_type=body.content_type,
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
            embedding_time_ms=result.embedding_time_ms,
            total_time_ms=result.total_time_ms,
        ),
        request_id=request_id,
    )
