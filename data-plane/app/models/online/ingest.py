from enum import Enum

from pydantic import BaseModel, Field, model_validator


class SearchMode(str, Enum):
    """Vector search strategy for Qdrant storage."""

    semantic = "semantic"
    hybrid = "hybrid"


class OnlineChunkingConfig(BaseModel):
    """Configuration for text chunking during ingestion."""

    strategy: str = Field("contextual", description="Chunking strategy: 'contextual' (recursive splitter + AI context prepended, default), 'recursive' (recursive character text splitter), 'late_chunking' (paragraph-aware), 'sentence' (sentence boundaries), or 'fixed' (character count)")
    max_chunk_size: int = Field(1200, ge=64, le=4096, description="Maximum chunk size in characters")
    overlap: int = Field(50, ge=0, le=512, description="Overlap between consecutive chunks in characters")


class OnlineVectorConfig(BaseModel):
    """Configuration for vector storage in Qdrant."""

    vector_size: int = Field(1536, ge=64, le=4096, description="Dimensionality of the dense (cosine) embedding vector. Must match the embedding model output. Default: 1536")
    search_mode: SearchMode = Field(SearchMode.semantic, description="'semantic' — store only dense (cosine) vectors for semantic search. 'hybrid' — store both dense (cosine) and sparse vectors for combined semantic + lexical search.")


class OnlineIngestMetadata(BaseModel):
    """Additional metadata attached to every vector in Qdrant."""

    assistant_id: str | None = Field(None, description="Identifier of the assistant that owns this content. At least one of assistant_id or municipality_id must be provided.")
    title: str | None = Field(None, description="Document/page title (shown in search results)")
    uploaded_by: str | None = Field(None, description="User or service that triggered the ingestion")
    source_type: str | None = Field("web", description="Origin: typically 'web' for online content")
    mime_type: str | None = Field(None, description="Original content MIME type")
    municipality_id: str | None = Field(None, description="Municipality/tenant identifier. At least one of assistant_id or municipality_id must be provided.")
    department: list[str] = Field(default_factory=list, description="Departments within the organization")

    @model_validator(mode="after")
    def check_at_least_one_id(self) -> "OnlineIngestMetadata":
        if not self.assistant_id and not self.municipality_id:
            raise ValueError("At least one of 'assistant_id' or 'municipality_id' must be provided")
        return self


class OnlineIngestRequest(BaseModel):
    """Request to ingest web-scraped content into the vector database.

    Takes scraped/parsed text and runs:
    chunks -> classifies -> embeds (BGE-M3) -> stores in Qdrant.

    Existing vectors for the same source_id are automatically replaced (upsert).

    **Vector modes:**
    - `semantic` (default) — stores only dense cosine vectors. Best for pure semantic similarity search.
    - `hybrid` — stores both dense cosine vectors and sparse vectors. Enables combined semantic + lexical (BM25-style) search for higher recall.
    """

    collection_name: str = Field(..., description="Qdrant collection name to store vectors in")
    source_id: str = Field(..., description="Unique document ID. Used for updates and deletes.")
    url: str = Field(..., description="Source URL (stored as source_url in Qdrant point metadata)")
    content: str = Field(..., min_length=1, description="Parsed/scraped text content (from /online/scrape or /online/document-parse)")
    language: str | None = Field(None, description="ISO 639-1 code. Auto-detected from content if omitted.")
    metadata: OnlineIngestMetadata = Field(..., description="Document metadata stored alongside vectors")
    chunking: OnlineChunkingConfig | None = Field(None, description="Override default chunking settings")
    vector_config: OnlineVectorConfig | None = Field(None, description="Override default vector storage settings (size, search mode)")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "collection_name": "wiener-neudorf",
                    "source_id": "web_foerderungen_001",
                    "url": "https://www.wiener-neudorf.gv.at/foerderungen",
                    "content": "Förderungen der Gemeinde Wiener Neudorf\n\nDie Gemeinde bietet verschiedene Förderungen...",
                    "language": "de",
                    "metadata": {
                        "assistant_id": "asst_wiener_neudorf_01",
                        "title": "Förderungen - Gemeinde Wiener Neudorf",
                        "source_type": "web",
                        "municipality_id": "wiener-neudorf",
                        "department": ["Bürgerservice", "Förderungen"],
                    },
                    "vector_config": {
                        "vector_size": 1536,
                        "search_mode": "semantic",
                    },
                }
            ]
        }
    }


class OnlineEntityCounts(BaseModel):
    """Count of entities extracted during classification."""

    dates: int = Field(0, description="Number of dates found")
    contacts: int = Field(0, description="Number of email addresses found")
    amounts: int = Field(0, description="Number of monetary amounts found")


class OnlineIngestData(BaseModel):
    """Result of the online ingest pipeline."""

    source_id: str = Field(..., description="Document ID that was ingested")
    chunks_created: int = Field(..., description="Number of text chunks created")
    vectors_stored: int = Field(..., description="Number of vectors stored in Qdrant")
    collection: str = Field(..., description="Qdrant collection name")
    content_type: list[str] = Field(..., description="Auto-detected content categories (e.g. ['funding', 'renewable_energy'])")
    entities_extracted: OnlineEntityCounts = Field(..., description="Entity extraction counts")
    embedding_time_ms: int = Field(..., description="Time spent on embedding (ms)")
    total_time_ms: int = Field(..., description="Total pipeline duration (ms)")
