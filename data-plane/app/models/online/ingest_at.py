"""Request/response models for ``POST /api/v1/online/ingest/at``.

The AT endpoint serves the Austrian funding assistant — a dedicated Qdrant
instance with per-province collections. Because the endpoint itself declares
the country and the assistant type, callers don't supply ``country``,
``collection_name``, ``assistant_type``, or ``vector_config`` (the schema is
fixed: one unnamed 1536-dim cosine vector, no sparse, no fallback).
"""

from pydantic import BaseModel, Field

from app.models.classify import ExtractedEntities
from app.models.online.ingest import OnlineChunkingConfig, OnlineIngestMetadata


class OnlineIngestATRequest(BaseModel):
    """Request to ingest funding content into the AT per-province collections.

    The funding extractor runs unconditionally to detect the applicable
    provinces. ``state_or_province`` (below) overrides the extractor's choice
    when supplied.
    """

    source_id: str = Field(..., description="Unique document ID. Used for idempotent overwrites keyed by source_url.")
    url: str = Field(..., description="Source URL (stored as metadata.source_url).")
    content: str = Field(..., min_length=1, description="Parsed/scraped text content from /online/scrape or /online/document-parse.")
    content_type: list[str] = Field(..., min_length=1, description="Content categories, e.g. ['funding','sport']. Obtained upstream from /online/scrape or /online/document-parse.")
    entities: ExtractedEntities | None = Field(None, description="Optional structured entities (dates, deadlines, amounts, contacts, departments) from the upstream scrape/parse call.")
    language: str | None = Field(None, description="ISO 639-1 code. Defaults to 'de' when omitted.")
    state_or_province: list[str] | None = Field(
        None,
        description=(
            "Optional explicit province override. Accepts either the English "
            "lowercase form ('lower austria', 'vienna') or the German "
            "collection name ('Niederösterreich', 'Wien'). When supplied, "
            "bypasses the funding-extractor's choice. When omitted or empty, "
            "the extractor decides; if the extractor also returns an empty "
            "list, the content fans out to all nine province collections."
        ),
    )
    metadata: OnlineIngestMetadata = Field(..., description="Document metadata stored alongside vectors.")
    chunking: OnlineChunkingConfig | None = Field(None, description="Override default chunking settings.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "source_id": "web_salzburg_sport_001",
                    "url": "https://www.salzburg.gv.at/sport-foerderung",
                    "content": "Sportförderung Salzburg\n\n...",
                    "content_type": ["funding", "sport"],
                    "language": "de",
                    "metadata": {
                        "assistant_id": "asst_foerder_at_01",
                        "municipality_id": "land-salzburg",
                        "title": "Sportförderung Salzburg",
                        "source_type": "web",
                    },
                }
            ]
        }
    }


class OnlineIngestATData(BaseModel):
    """Result of the AT ingest fan-out."""

    source_id: str = Field(..., description="Document ID that was ingested.")
    chunks_created: int = Field(..., description="Number of text chunks produced from the content.")
    vectors_stored: int = Field(..., description="Total vectors stored across every target collection.")
    collections_written: list[str] = Field(..., description="German collection names that received the ingest (e.g. ['Tirol'], or all nine on a nationwide fan-out).")
    content_type: list[str] = Field(..., description="Content categories passed through from the request.")
    embedding_time_ms: int = Field(..., description="Time spent on embedding (ms).")
    total_time_ms: int = Field(..., description="Total pipeline duration (ms).")
