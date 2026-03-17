"""Ingest pipeline — chunks → classifies → embeds → stores in Qdrant."""

import time
import uuid

from app.config import settings
from app.services.embedding.bge_m3_client import EmbeddingError
from app.services.embedding.qdrant_service import QdrantError, QdrantService
from app.services.intelligence.chunker import Chunker
from app.services.intelligence.classifier import Classifier
from app.utils.logger import get_logger

log = get_logger(__name__)


class IngestError(Exception):
    def __init__(self, message: str, code: str = "EMBEDDING_FAILED"):
        super().__init__(message)
        self.code = code


class IngestResult:
    def __init__(
        self,
        source_id: str,
        chunks_created: int,
        vectors_stored: int,
        collection: str,
        classification: list[str],
        entities_extracted: dict,
        embedding_time_ms: int,
        total_time_ms: int,
    ):
        self.source_id = source_id
        self.chunks_created = chunks_created
        self.vectors_stored = vectors_stored
        self.collection = collection
        self.classification = classification
        self.entities_extracted = entities_extracted
        self.embedding_time_ms = embedding_time_ms
        self.total_time_ms = total_time_ms


class IngestService:
    """Orchestrates the full ingest pipeline: chunk → classify → embed → store."""

    def __init__(
        self,
        chunker: Chunker,
        classifier: Classifier,
        embedder,
        qdrant: QdrantService,
    ) -> None:
        self._chunker = chunker
        self._classifier = classifier
        self._embedder = embedder
        self._qdrant = qdrant

    async def ingest(
        self,
        source_id: str,
        file_path: str,
        content: str,
        acl: dict | None,
        metadata: dict,
        collection_name: str,
        language: str | None = None,
        chunking_strategy: str = "late_chunking",
        max_chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        vector_size: int = 1536,
        search_mode: str = "semantic",
    ) -> IngestResult:
        start = time.monotonic()
        collection = collection_name
        use_sparse = search_mode == "hybrid"

        if not collection:
            raise IngestError("collection_name is required", code="QDRANT_COLLECTION_NOT_FOUND")

        # Ensure collection exists with correct vector config
        try:
            await self._qdrant.create_collection(
                name=collection,
                dense_dim=vector_size,
                sparse=use_sparse,
                distance="Cosine",
            )
        except QdrantError as e:
            raise IngestError(str(e), code="QDRANT_CONNECTION_FAILED") from e

        # 1. Chunk
        chunk_result = self._chunker.chunk(
            text=content,
            strategy=chunking_strategy,
            max_chunk_size=max_chunk_size or settings.default_chunk_size,
            overlap=chunk_overlap if chunk_overlap is not None else settings.default_chunk_overlap,
        )

        if not chunk_result.chunks:
            raise IngestError("Content produced no chunks", code="VALIDATION_EMPTY_CONTENT")

        log.info("ingest_chunked", source_id=source_id, chunks=chunk_result.total_chunks)

        # 2. Classify (on full content for better accuracy)
        try:
            classify_result = await self._classifier.classify(content, language=language or "de")
        except Exception as e:
            log.warning("ingest_classify_fallback", source_id=source_id, error=str(e))
            classify_result = None

        if classify_result:
            classification = [classify_result.category.value] + classify_result.sub_categories
        else:
            classification = ["general"]
        entities_extracted = {
            "dates": len(classify_result.entities.dates) if classify_result else 0,
            "contacts": len(classify_result.entities.contacts) if classify_result else 0,
            "amounts": len(classify_result.entities.amounts) if classify_result else 0,
        }

        # 3. Embed all chunks
        embed_start = time.monotonic()
        try:
            embeddings = await self._embedder.embed_batch(chunk_result.chunks)
        except EmbeddingError as e:
            error_msg = str(e).lower()
            if "oom" in error_msg or "memory" in error_msg:
                raise IngestError(str(e), code="EMBEDDING_OOM") from e
            if "not initialized" in error_msg or "not loaded" in error_msg:
                raise IngestError(str(e), code="EMBEDDING_MODEL_NOT_LOADED") from e
            raise IngestError(str(e), code="EMBEDDING_FAILED") from e

        embedding_time_ms = int((time.monotonic() - embed_start) * 1000)
        log.info("ingest_embedded", source_id=source_id, chunks=len(embeddings), duration_ms=embedding_time_ms)

        # 4. Build Qdrant points
        points = []
        for i, (chunk_text, embedding) in enumerate(zip(chunk_result.chunks, embeddings)):
            chunk_id = f"{source_id}_chunk_{i:04d}"
            point_metadata = {
                "chunk_id": chunk_id,
                "source_id": source_id,
                "chunk_index": i,
                "source_url": metadata.get("source_url", ""),
                "source_path": file_path,
                "content_type": classification,
                "language": language or "de",
                "assistant_id": metadata.get("assistant_id", ""),
                "title": metadata.get("title", ""),
                "source_type": metadata.get("source_type", ""),
                "mime_type": metadata.get("mime_type", ""),
                "uploaded_by": metadata.get("uploaded_by", ""),
                "organization_id": metadata.get("organization_id", ""),
                "department": metadata.get("department", []),
            }

            # ACL fields (when provided)
            if acl:
                point_metadata.update({
                    "acl_allow_groups": acl.get("allow_groups", []),
                    "acl_deny_groups": acl.get("deny_groups", []),
                    "acl_allow_roles": acl.get("allow_roles", []),
                    "acl_allow_users": acl.get("allow_users", []),
                    "acl_visibility": acl.get("visibility", "public"),
                    "acl_department": acl.get("department", ""),
                })

            # Add entity data if available
            if classify_result:
                point_metadata["entity_amounts"] = classify_result.entities.amounts[:5]
                point_metadata["entity_deadlines"] = classify_result.entities.deadlines[:5]

            payload = {
                "content": chunk_text,
                "metadata": point_metadata,
            }

            vectors: dict = {"dense": embedding.dense}

            # Include sparse vector for hybrid search mode
            if use_sparse and embedding.sparse:
                indices = sorted(embedding.sparse.keys())
                vectors["sparse"] = {
                    "indices": indices,
                    "values": [embedding.sparse[idx] for idx in indices],
                }

            point = {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id)),
                "vector": vectors,
                "payload": payload,
            }
            points.append(point)

        # 5. Delete old vectors for this source_id, then upsert new ones
        try:
            await self._qdrant.delete_by_source_id(collection, source_id)
        except QdrantError:
            pass  # OK if nothing to delete

        try:
            vectors_stored = await self._qdrant.upsert_points(collection, points)
        except QdrantError as e:
            error_msg = str(e).lower()
            if "disk" in error_msg or "full" in error_msg:
                raise IngestError(str(e), code="QDRANT_DISK_FULL") from e
            if "not found" in error_msg:
                raise IngestError(str(e), code="QDRANT_COLLECTION_NOT_FOUND") from e
            raise IngestError(str(e), code="QDRANT_UPSERT_FAILED") from e

        total_time_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "ingest_complete",
            source_id=source_id,
            chunks=chunk_result.total_chunks,
            vectors=vectors_stored,
            collection=collection,
            total_ms=total_time_ms,
        )

        return IngestResult(
            source_id=source_id,
            chunks_created=chunk_result.total_chunks,
            vectors_stored=vectors_stored,
            collection=collection,
            classification=classification,
            entities_extracted=entities_extracted,
            embedding_time_ms=embedding_time_ms,
            total_time_ms=total_time_ms,
        )
