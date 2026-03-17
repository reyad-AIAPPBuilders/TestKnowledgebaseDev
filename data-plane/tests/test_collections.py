"""Tests for POST /api/v1/collections/init and GET /api/v1/collections/stats."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.embedding.qdrant_service import QdrantError


@pytest.fixture
def mock_qdrant():
    qdrant = MagicMock()
    qdrant.create_collection = AsyncMock()
    qdrant.collection_stats = AsyncMock()
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


# ── POST /collections/init ──────────────────────────────────────────


def test_init_collection_created(client, mock_qdrant):
    mock_qdrant.create_collection.return_value = True

    response = client.post("/api/v1/collections/init", json={
        "collection_name": "wiener-neudorf",
        "vector_config": {"dense_dim": 1024, "sparse": True, "distance": "cosine"},
    })
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["collection"] == "wiener-neudorf"
    assert data["data"]["created"] is True
    assert data["data"]["dense_dim"] == 1024
    assert data["data"]["sparse_enabled"] is True
    assert data["request_id"]


def test_init_collection_already_exists(client, mock_qdrant):
    mock_qdrant.create_collection.return_value = False

    response = client.post("/api/v1/collections/init", json={
        "collection_name": "existing-collection",
    })
    data = response.json()
    assert data["success"] is True
    assert data["data"]["created"] is False


def test_init_collection_default_config(client, mock_qdrant):
    mock_qdrant.create_collection.return_value = True

    response = client.post("/api/v1/collections/init", json={
        "collection_name": "test-municipality",
    })
    data = response.json()
    assert data["success"] is True
    assert data["data"]["dense_dim"] == 1024
    assert data["data"]["sparse_enabled"] is True


def test_init_collection_connection_failed(client, mock_qdrant):
    mock_qdrant.create_collection.side_effect = QdrantError("Qdrant connection failed")

    response = client.post("/api/v1/collections/init", json={
        "collection_name": "fail-collection",
    })
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "QDRANT_CONNECTION_FAILED"


def test_init_collection_request_id(client, mock_qdrant):
    mock_qdrant.create_collection.return_value = True

    response = client.post(
        "/api/v1/collections/init",
        json={"collection_name": "test"},
        headers={"X-Request-ID": "coll-init-789"},
    )
    data = response.json()
    assert data["request_id"] == "coll-init-789"
    assert response.headers["X-Request-ID"] == "coll-init-789"


# ── GET /collections/stats ───────────────────────────────────────────


def test_collection_stats_success(client, mock_qdrant):
    mock_qdrant.collection_stats.return_value = {
        "vectors_count": 12450,
        "points_count": 12450,
        "segments_count": 3,
        "disk_data_size": 245 * 1024 * 1024,
    }

    response = client.get("/api/v1/collections/stats?collection_name=wiener-neudorf")

    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["collection"] == "wiener-neudorf"
    assert data["data"]["total_vectors"] == 12450
    assert data["data"]["disk_usage_mb"] == 245.0


def test_collection_stats_no_collection_configured(client):
    """Missing collection_name query param should return 422."""
    response = client.get("/api/v1/collections/stats")
    assert response.status_code == 422


def test_collection_stats_not_found(client, mock_qdrant):
    mock_qdrant.collection_stats.side_effect = QdrantError("Collection not found: missing")

    response = client.get("/api/v1/collections/stats?collection_name=missing")

    data = response.json()
    assert data["success"] is False
    assert data["error"] == "QDRANT_COLLECTION_NOT_FOUND"


def test_collection_stats_connection_failed(client, mock_qdrant):
    mock_qdrant.collection_stats.side_effect = QdrantError("Qdrant connection failed")

    response = client.get("/api/v1/collections/stats?collection_name=test-collection")

    data = response.json()
    assert data["success"] is False
    assert data["error"] == "QDRANT_CONNECTION_FAILED"
