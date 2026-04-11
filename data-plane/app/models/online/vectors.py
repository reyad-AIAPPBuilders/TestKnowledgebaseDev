from pydantic import BaseModel, Field


class OnlineMetadataFilter(BaseModel):
    """A single metadata filter condition."""

    key: str = Field(..., description="Metadata field name (e.g. 'source_type', 'content_type', 'assistant_id', 'municipality_id')")
    value: str = Field(..., description="Exact value to match")


class OnlineDeleteByFilterRequest(BaseModel):
    """Request to delete vectors matching metadata filters.

    All filters are combined with AND logic — only points matching every
    condition are deleted.
    """

    collection_name: str = Field(..., description="Qdrant collection name")
    filters: list[OnlineMetadataFilter] = Field(..., min_length=1, description="Metadata conditions (AND logic). At least one filter is required.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "collection_name": "wiener-neudorf",
                    "filters": [
                        {"key": "assistant_id", "value": "asst_wiener_neudorf_01"},
                        {"key": "content_type", "value": "funding"},
                    ],
                },
            ]
        }
    }


class OnlineDeleteByFilterData(BaseModel):
    """Result of deleting vectors by metadata filter."""

    vectors_deleted: int = Field(..., description="Number of vectors removed from Qdrant")
    filters_applied: list[OnlineMetadataFilter] = Field(..., description="Filters that were used")


class OnlineDeleteVectorsData(BaseModel):
    """Result of deleting all vectors for a document."""

    source_id: str = Field(..., description="Document ID whose vectors were deleted")
    vectors_deleted: int = Field(..., description="Number of vectors removed from Qdrant")
