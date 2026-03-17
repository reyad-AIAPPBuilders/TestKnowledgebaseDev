"""Tests for DELETE /api/v1/local/vectors/{source_id} and PUT /api/v1/local/vectors/update-acl."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.embedding.qdrant_service import QdrantError


@pytest.fixture
def mock_qdrant():
    qdrant = MagicMock()
    qdrant.delete_by_source_id = AsyncMock()
    qdrant.update_payload = AsyncMock()
    return qdrant


@pytest.fixture
def client(mock_qdrant):
    app.state._test_mode = True
    app.state.qdrant = mock_qdrant
    app.state.embedder = MagicMock()
    app.state.scraping = MagicMock()
    app.state.sitemap_parser = MagicMock()
    app.state.parser = MagicMock()
    app.state.classifier = MagicMock()
    with TestClient(app) as c:
        yield c


# ── DELETE /local/vectors/{source_id} ──────────────────────────────────────


def test_delete_vectors_success(client, mock_qdrant):
    mock_qdrant.delete_by_source_id.return_value = 47

    response = client.delete("/api/v1/local/vectors/doc_abc123?collection_name=wiener-neudorf")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["source_id"] == "doc_abc123"
    assert data["data"]["vectors_deleted"] == 47
    assert data["request_id"]


def test_delete_vectors_connection_failed(client, mock_qdrant):
    mock_qdrant.delete_by_source_id.side_effect = QdrantError("Qdrant connection failed: refused")

    response = client.delete("/api/v1/local/vectors/doc_abc123?collection_name=wiener-neudorf")
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "QDRANT_CONNECTION_FAILED"


def test_delete_vectors_delete_failed(client, mock_qdrant):
    mock_qdrant.delete_by_source_id.side_effect = QdrantError("Delete failed: internal error")

    response = client.delete("/api/v1/local/vectors/doc_abc123?collection_name=wiener-neudorf")
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "QDRANT_DELETE_FAILED"


def test_delete_vectors_request_id(client, mock_qdrant):
    mock_qdrant.delete_by_source_id.return_value = 0

    response = client.delete(
        "/api/v1/local/vectors/doc_xyz?collection_name=wiener-neudorf",
        headers={"X-Request-ID": "vec-del-123"},
    )
    data = response.json()
    assert data["request_id"] == "vec-del-123"
    assert response.headers["X-Request-ID"] == "vec-del-123"


# ── PUT /local/vectors/update-acl ──────────────────────────────────────────


def test_update_acl_success(client, mock_qdrant):
    mock_qdrant.update_payload.return_value = 47

    response = client.put("/api/v1/local/vectors/update-acl", json={
        "collection_name": "wiener-neudorf",
        "source_id": "doc_abc123",
        "acl": {
            "allow_groups": ["DOMAIN\\Bauamt-Mitarbeiter"],
            "deny_groups": [],
            "visibility": "internal",
        },
    })
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["source_id"] == "doc_abc123"
    assert data["data"]["vectors_updated"] == 47


def test_update_acl_connection_failed(client, mock_qdrant):
    mock_qdrant.update_payload.side_effect = QdrantError("Qdrant connection failed: timeout")

    response = client.put("/api/v1/local/vectors/update-acl", json={
        "collection_name": "wiener-neudorf",
        "source_id": "doc_abc123",
        "acl": {
            "allow_groups": [],
            "deny_groups": [],
            "visibility": "public",
        },
    })
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "QDRANT_CONNECTION_FAILED"


def test_update_acl_upsert_failed(client, mock_qdrant):
    mock_qdrant.update_payload.side_effect = QdrantError("Payload update failed: bad request")

    response = client.put("/api/v1/local/vectors/update-acl", json={
        "collection_name": "wiener-neudorf",
        "source_id": "doc_abc123",
        "acl": {
            "allow_groups": [],
            "deny_groups": [],
            "visibility": "restricted",
        },
    })
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "QDRANT_UPSERT_FAILED"


def test_update_acl_invalid_visibility(client):
    """Pydantic should reject invalid visibility values."""
    response = client.put("/api/v1/local/vectors/update-acl", json={
        "collection_name": "wiener-neudorf",
        "source_id": "doc_abc123",
        "acl": {
            "allow_groups": [],
            "deny_groups": [],
            "visibility": "secret",
        },
    })
    assert response.status_code == 422
