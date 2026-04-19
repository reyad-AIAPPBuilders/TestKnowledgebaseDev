"""POST /api/v1/online/ingest/at — AT funding-assistant ingest.

Dedicated endpoint for the Austrian funding-assistant pipeline. Runs on a
separate Qdrant instance (``app.state.qdrant_at``) whose collections are
pre-created with the legacy single-unnamed-vector schema::

    {"vectors": {"size": 1536, "distance": "Cosine"}}

— no sparse, no multi-vector — with ``metadata.region`` and
``metadata.source_url`` indexed as keyword fields. Because of that schema
mismatch with the rest of the platform, this endpoint drives the ingest
directly instead of reusing :class:`IngestService`.

Behaviour
---------
- Country is implicit — every request is treated as AT.
- Assistant type is implicit — the funding extractor always runs, so callers
  do not supply ``assistant_type``.
- Province selection precedence:
  1. ``body.state_or_province`` (explicit request override) — accepts either
     English lowercase (``"lower austria"``) or the German collection name
     (``"Niederösterreich"``).
  2. Funding-extractor output.
  3. Fallback: fan out to all nine Austrian province collections.
- Idempotency: strict-mode collections block filtering on unindexed keys, so
  ``metadata.source_id`` cannot be used to delete old chunks. The flow
  deletes by the indexed ``metadata.source_url`` field before upserting.
"""

import asyncio
import hashlib
import time

from fastapi import APIRouter, Request

from app.config import settings
from app.models.common import ErrorCode, ResponseEnvelope
from app.models.online.ingest_at import OnlineIngestATData, OnlineIngestATRequest
from app.services.embedding.bge_m3_client import EmbeddingError
from app.services.embedding.qdrant_service import QdrantError
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/online", tags=["Online - Ingestion Pipeline (AT)"])


# English-lowercase (as emitted by the funding extractor) → German collection name.
PROVINCE_TO_COLLECTION_AT: dict[str, str] = {
    "burgenland": "Burgenland",
    "carinthia": "Kärnten",
    "lower austria": "Niederösterreich",
    "upper austria": "Oberösterreich",
    "salzburg": "Salzburg",
    "styria": "Steiermark",
    "tyrol": "Tirol",
    "vorarlberg": "Vorarlberg",
    "vienna": "Wien",
}

ALL_AT_COLLECTIONS: list[str] = list(PROVINCE_TO_COLLECTION_AT.values())

_AT_COUNTRY = "AT"
_AT_ASSISTANT_TYPE = "funding"
# Sentinel stored in metadata.region when a document fans out to every
# Austrian province (nationwide funding). Must match the value the search
# side filters on.
_REGION_ALL = "alle"


def _resolve_collection(name: str) -> str | None:
    if not name:
        return None
    lower = name.strip().lower()
    if lower in PROVINCE_TO_COLLECTION_AT:
        return PROVINCE_TO_COLLECTION_AT[lower]
    for collection in ALL_AT_COLLECTIONS:
        if collection.lower() == lower:
            return collection
    return None


def _normalize_provinces(names: list[str] | None) -> list[str]:
    """Map a mixed list of province names to their German collection forms.

    Dedupes, preserves stable order, and drops anything that doesn't resolve
    to one of the nine AT province collections.
    """
    if not names:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        resolved = _resolve_collection(raw)
        if resolved and resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return sorted(out)


def _select_collections(
    override_resolved: list[str],
    extracted_resolved: list[str],
) -> list[str]:
    """Pick target collections from the already-resolved province lists."""
    if override_resolved:
        return list(override_resolved)
    if extracted_resolved:
        return list(extracted_resolved)
    return list(ALL_AT_COLLECTIONS)


async def _safe_extract_funding(
    extractor, content: str, *, source_url: str, source_id: str
) -> dict:
    try:
        return await extractor.extract(content, source_url=source_url, country=_AT_COUNTRY)
    except Exception as e:
        log.warning("ingest_online_at_funding_extract_failed", source_id=source_id, error=str(e))
        return {}


def _point_id(source_id: str, chunk_index: int, collection: str) -> int:
    """Deterministic 64-bit unsigned integer ID for a chunk in a given collection.

    The existing AT collections use integer point IDs (the data already carries
    small sequential values like ``474``). We derive ours from the low 64 bits
    of ``sha256(collection|source_id|chunk_index)``:

    - Stable across runs → repeated ingest overwrites in place (idempotent).
    - Per-collection salt → the same chunk in different province collections
      gets different IDs, which is irrelevant for correctness but prevents any
      cross-collection coincidence from looking like a duplicate.
    - 64-bit values are astronomically unlikely to collide with the existing
      small sequential IDs already present in the collections.
    """
    key = f"{collection}|{source_id}|{chunk_index}".encode()
    digest = hashlib.sha256(key).digest()
    return int.from_bytes(digest[:8], "big")


_KNOWN_METADATA_KEYS = {
    "source_id", "source_url",
    "content_type", "language", "title", "source_type",
    "uploaded_by", "assistant_id", "municipality_id", "department",
    "assistant_type", "region",
}


def _build_point(
    *,
    chunk_text: str,
    chunk_index: int,
    embedding: list[float],
    source_id: str,
    source_url: str,
    content_type: list[str],
    language: str,
    collection: str,
    region: list[str],
    metadata: dict,
    entities: dict | None,
) -> dict:
    """Build a single Qdrant point in the AT collection's unnamed-vector schema."""
    point_metadata: dict = {
        "source_id": source_id,
        "source_url": source_url,
        "region": region,
        "content_type": content_type,
        "language": language,
        "title": metadata.get("title", ""),
        "source_type": metadata.get("source_type", ""),
        "uploaded_by": metadata.get("uploaded_by", ""),
    }

    if entities:
        if entities.get("dates"):
            point_metadata["entity_dates"] = entities["dates"][:10]
        if entities.get("deadlines"):
            point_metadata["entity_deadlines"] = entities["deadlines"][:5]
        if entities.get("amounts"):
            point_metadata["entity_amounts"] = entities["amounts"][:10]
        if entities.get("contacts"):
            point_metadata["entity_contacts"] = entities["contacts"][:10]
        if entities.get("departments"):
            point_metadata["entity_departments"] = entities["departments"][:5]

    for key, value in metadata.items():
        if key not in _KNOWN_METADATA_KEYS and value is not None:
            point_metadata[key] = value

    payload = {
        "municipality_id": metadata.get("municipality_id", ""),
        "assistant_id": metadata.get("assistant_id", ""),
        "department": metadata.get("department", []),
        "content": chunk_text,
        "metadata": point_metadata,
    }

    return {
        "id": _point_id(source_id, chunk_index, collection),
        "vector": embedding,
        "payload": payload,
    }


async def _delete_existing_by_source_url(qdrant, collection: str, source_url: str) -> None:
    """Best-effort delete of prior points for this source_url. Swallows errors.

    ``metadata.source_url`` is indexed on the AT collections, so delete-by-filter
    is permitted under strict-mode. ``metadata.source_id`` is *not* indexed, so
    it cannot be used here.
    """
    try:
        await qdrant.delete_by_filter(
            collection,
            {"must": [{"key": "metadata.source_url", "match": {"value": source_url}}]},
        )
    except QdrantError as e:
        log.warning("ingest_online_at_delete_skipped", collection=collection, error=str(e))


@router.post(
    "/ingest/at",
    summary="Ingest funding content into the AT per-province collections",
    description=(
        "AT funding-assistant ingest. The country (AT) and assistant type "
        "(funding) are implicit — do not pass them in the body.\n\n"
        "**Flow:** chunk → optional contextual enrichment → OpenAI embed "
        "(1536-dim, cosine) → upsert to each target province collection on "
        "the AT Qdrant instance.\n\n"
        "**Province selection:**\n"
        "1. `state_or_province` override (German or English lowercase forms).\n"
        "2. Funding extractor's `state_or_province` output.\n"
        "3. All nine province collections as the nationwide fallback: "
        "`Burgenland`, `Kärnten`, `Niederösterreich`, `Oberösterreich`, "
        "`Salzburg`, `Steiermark`, `Tirol`, `Vorarlberg`, `Wien`.\n\n"
        "**Idempotency:** prior points for the same `url` are deleted via the "
        "indexed `metadata.source_url` field before upsert. Point IDs are a "
        "deterministic uuid5 over `source_id`+chunk index, so repeat ingests "
        "overwrite in place.\n\n"
        "**Qdrant target:** uses `QDRANT_URL_AT` / `QDRANT_PORT_AT` / `QDRANT_API_KEY_AT` "
        "when set, falling back to the default Qdrant endpoint otherwise. "
        "`QDRANT_PORT_AT` has no default — leave it unset when the port is "
        "already embedded in `QDRANT_URL_AT`.\n\n"
        "**Optional X-API-Key header** — required only when `DP_ONLINE_API_KEYS` is configured.\n\n"
        "**Error codes:** `VALIDATION_EMPTY_CONTENT`, `EMBEDDING_MODEL_NOT_LOADED`, "
        "`EMBEDDING_FAILED`, `EMBEDDING_OOM`, `QDRANT_CONNECTION_FAILED`, "
        "`QDRANT_COLLECTION_NOT_FOUND`, `QDRANT_UPSERT_FAILED`, `QDRANT_DISK_FULL`."
    ),
    response_description="Ingestion result with the province collections written to and timing info.",
)
async def ingest_online_at(
    body: OnlineIngestATRequest,
    request: Request,
) -> ResponseEnvelope[OnlineIngestATData]:
    request_id = request.state.request_id
    started = time.monotonic()

    if not body.content.strip():
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.VALIDATION_EMPTY_CONTENT,
            detail="Content must not be empty",
            request_id=request_id,
        )

    log.info(
        "ingest_online_at_received",
        source_id=body.source_id,
        url=body.url,
    )

    chunker = request.app.state.chunker
    contextual_enricher = request.app.state.contextual_enricher
    openai_embedder = request.app.state.openai_embedder
    qdrant = request.app.state.qdrant_at
    extractor = request.app.state.funding_extractor

    # ── 1. Funding extraction (once) ──
    extracted = await _safe_extract_funding(
        extractor,
        body.content,
        source_url=body.url,
        source_id=body.source_id,
    )

    # ── 2. Target collections ──
    # Normalize both override and extractor output to the German collection
    # names so the stored metadata.state_or_province matches the collections
    # written, regardless of what casing/language the caller provided.
    override_raw = body.state_or_province or []
    extracted_raw = extracted.get("state_or_province", []) if extracted else []
    override_resolved = _normalize_provinces(override_raw)
    extracted_resolved = _normalize_provinces(extracted_raw)
    target_collections = _select_collections(
        override_resolved=override_resolved,
        extracted_resolved=extracted_resolved,
    )
    log.info(
        "ingest_online_at_fanout",
        source_id=body.source_id,
        collections=target_collections,
        extracted_raw=extracted_raw,
        extracted_resolved=extracted_resolved,
        override_raw=override_raw,
        override_resolved=override_resolved,
    )

    # ── 3. Chunk (+ optional contextual enrichment) ──
    chunking = body.chunking
    strategy = chunking.strategy if chunking else "contextual"
    use_contextual = strategy == "contextual"
    base_strategy = "recursive" if use_contextual else strategy

    chunk_result = chunker.chunk(
        text=body.content,
        strategy=base_strategy,
        max_chunk_size=chunking.max_chunk_size if chunking else settings.default_chunk_size,
        overlap=(chunking.overlap if chunking and chunking.overlap is not None else settings.default_chunk_overlap),
    )
    if not chunk_result.chunks:
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.VALIDATION_EMPTY_CONTENT,
            detail="Content produced no chunks",
            request_id=request_id,
        )

    chunks = chunk_result.chunks
    if use_contextual and contextual_enricher:
        try:
            chunks = await contextual_enricher.enrich_chunks(document=body.content, chunks=chunks)
        except Exception as e:
            log.warning("ingest_online_at_contextual_failed", source_id=body.source_id, error=str(e))

    # ── 4. Embed once via OpenAI (unnamed 1536-dim cosine vector) ──
    embed_start = time.monotonic()
    try:
        embeddings = await openai_embedder.embed_batch(chunks)
    except EmbeddingError as e:
        msg = str(e).lower()
        if "oom" in msg or "memory" in msg:
            code = ErrorCode.EMBEDDING_OOM
        elif "not initialized" in msg or "not loaded" in msg or "not configured" in msg:
            code = ErrorCode.EMBEDDING_MODEL_NOT_LOADED
        else:
            code = ErrorCode.EMBEDDING_FAILED
        log.error("ingest_online_at_embed_failed", source_id=body.source_id, error=str(e))
        return ResponseEnvelope(
            success=False,
            error=code,
            detail=str(e),
            request_id=request_id,
        )
    embedding_time_ms = int((time.monotonic() - embed_start) * 1000)

    # ── 5. Build per-collection points ──
    base_metadata = body.metadata.model_dump()
    base_metadata["source_url"] = body.url
    base_metadata["assistant_type"] = _AT_ASSISTANT_TYPE
    # Funding-extractor output merged under request metadata so request wins.
    merged_metadata = {**extracted, **base_metadata} if extracted else base_metadata
    # Always store state_or_province in the German collection-name form so it
    # matches the target collections written (consistent across override vs.
    # extractor paths, and across mixed English/German caller input).
    stored_states = override_resolved or extracted_resolved or list(target_collections)
    merged_metadata["state_or_province"] = stored_states

    entities = body.entities.model_dump() if body.entities else None
    language = body.language or "de"

    # When fan-out covers every Austrian province (nationwide funding), mark
    # each point with the ["alle"] sentinel instead of the specific German
    # collection name. Otherwise every point gets a single-element array
    # naming the collection it lives in.
    is_nationwide = set(target_collections) == set(ALL_AT_COLLECTIONS)

    async def _upsert_one(collection: str) -> int:
        await _delete_existing_by_source_url(qdrant, collection, body.url)

        region_value = [_REGION_ALL] if is_nationwide else [collection]

        points = [
            _build_point(
                chunk_text=chunk_text,
                chunk_index=i,
                embedding=embeddings[i].dense,
                source_id=body.source_id,
                source_url=body.url,
                content_type=body.content_type,
                language=language,
                collection=collection,
                region=region_value,
                metadata=merged_metadata,
                entities=entities,
            )
            for i, chunk_text in enumerate(chunks)
        ]
        return await qdrant.upsert_points(collection, points)

    try:
        per_collection_counts = await asyncio.gather(*(_upsert_one(c) for c in target_collections))
    except QdrantError as e:
        msg = str(e).lower()
        if "disk" in msg or "full" in msg:
            code = ErrorCode.QDRANT_DISK_FULL
        elif "not found" in msg:
            code = ErrorCode.QDRANT_COLLECTION_NOT_FOUND
        elif "connection" in msg:
            code = ErrorCode.QDRANT_CONNECTION_FAILED
        else:
            code = ErrorCode.QDRANT_UPSERT_FAILED
        log.error("ingest_online_at_upsert_failed", source_id=body.source_id, error=str(e))
        return ResponseEnvelope(
            success=False,
            error=code,
            detail=str(e),
            request_id=request_id,
        )

    total_vectors = sum(per_collection_counts)
    total_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "ingest_online_at_complete",
        source_id=body.source_id,
        chunks=len(chunks),
        collections=target_collections,
        vectors_stored=total_vectors,
        total_ms=total_ms,
    )

    return ResponseEnvelope(
        success=True,
        data=OnlineIngestATData(
            source_id=body.source_id,
            chunks_created=len(chunks),
            vectors_stored=total_vectors,
            collections_written=target_collections,
            content_type=body.content_type,
            embedding_time_ms=embedding_time_ms,
            total_time_ms=total_ms,
        ),
        request_id=request_id,
    )
