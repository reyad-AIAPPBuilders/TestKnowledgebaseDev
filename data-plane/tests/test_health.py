"""Tests for GET /api/v1/health and GET /api/v1/ready."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    app.state._test_mode = True
    with TestClient(app) as c:
        yield c


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data


def test_ready_minimal(client):
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data.get("services") is None
    assert "uptime_seconds" in data


def test_ready_full_all_healthy(client):
    """Authenticated ready check with all services healthy."""
    # Set up mock services
    mock_scraping = MagicMock()
    mock_scraping.is_ready = True

    mock_qdrant = MagicMock()
    mock_qdrant.check_health = AsyncMock(return_value=True)

    mock_embedder = MagicMock()
    mock_embedder.check_health = AsyncMock(return_value=True)

    mock_parser = MagicMock()
    mock_parser.check_health = AsyncMock(return_value=True)

    mock_cache = MagicMock()
    mock_cache.ping = AsyncMock(return_value=True)

    app.state.scraping = mock_scraping
    app.state.qdrant = mock_qdrant
    app.state.embedder = mock_embedder
    app.state.parser = mock_parser
    app.state.cache = mock_cache

    response = client.get(
        "/api/v1/ready",
        headers={"X-Signature": "dummy-for-full-check"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data["services"]["crawl4ai"] is True
    assert data["services"]["qdrant"] is True
    assert data["services"]["bge_m3"] is True
    assert data["services"]["parser"] is True
    assert data["services"]["redis"] is True
    assert data["version"]
    assert data["mode"]


def test_ready_full_service_down(client):
    """Authenticated ready check with one core service down."""
    mock_scraping = MagicMock()
    mock_scraping.is_ready = True

    mock_qdrant = MagicMock()
    mock_qdrant.check_health = AsyncMock(return_value=False)  # Qdrant down

    mock_embedder = MagicMock()
    mock_embedder.check_health = AsyncMock(return_value=True)

    mock_parser = MagicMock()
    mock_parser.check_health = AsyncMock(return_value=True)

    app.state.scraping = mock_scraping
    app.state.qdrant = mock_qdrant
    app.state.embedder = mock_embedder
    app.state.parser = mock_parser

    response = client.get(
        "/api/v1/ready",
        headers={"X-Signature": "dummy"},
    )
    data = response.json()
    assert data["ready"] is False
    assert data["services"]["qdrant"] is False
    assert data["services"]["bge_m3"] is True


def test_ready_full_service_exception(client):
    """Health check that throws should result in service=false, not 500."""
    mock_qdrant = MagicMock()
    mock_qdrant.check_health = AsyncMock(side_effect=Exception("connection refused"))

    app.state.scraping = MagicMock(is_ready=True)
    app.state.qdrant = mock_qdrant
    app.state.embedder = MagicMock(check_health=AsyncMock(return_value=True))
    app.state.parser = MagicMock(check_health=AsyncMock(return_value=True))

    response = client.get(
        "/api/v1/ready",
        headers={"X-Signature": "dummy"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["services"]["qdrant"] is False


def test_request_id_header(client):
    response = client.get("/api/v1/health")
    assert "X-Request-ID" in response.headers


def test_request_id_echo(client):
    response = client.get("/api/v1/health", headers={"X-Request-ID": "test-123"})
    assert response.headers["X-Request-ID"] == "test-123"
