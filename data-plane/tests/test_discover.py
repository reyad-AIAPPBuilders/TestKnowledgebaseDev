"""Tests for POST /api/v1/local/discover endpoint and discovery services."""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.discovery.discovery_service import DiscoveryError, DiscoveryResult, DiscoveryService
from app.services.discovery.smb_client import DiscoveredFile, SMBClient


@pytest.fixture
def mock_discovery():
    svc = MagicMock()
    svc.discover = AsyncMock()
    return svc


@pytest.fixture
def client(mock_discovery):
    app.state._test_mode = True
    app.state.discovery = mock_discovery
    app.state.scraping = MagicMock()
    app.state.sitemap_parser = MagicMock()
    app.state.parser = MagicMock()
    app.state.classifier = MagicMock()
    app.state.embedder = MagicMock()
    app.state.qdrant = MagicMock()
    with TestClient(app) as c:
        yield c


def _make_result(files, since_hash_map=None):
    """Helper to create a DiscoveryResult with proper status assignment."""
    since_hash_map = since_hash_map or {}
    return DiscoveryResult(files=files, since_hash_map=since_hash_map)


# ── Endpoint tests ──────────────────────────────────────────────────


def test_discover_smb_success(client, mock_discovery):
    files = [
        DiscoveredFile(
            path="//server/bauamt/antrag.pdf",
            file_hash="sha256:abc123",
            size_bytes=245000,
            mime_type="application/pdf",
            last_modified="2025-03-01T10:30:00+00:00",
            acl={"source": "ntfs", "allow_groups": ["DOMAIN\\Bauamt"], "deny_groups": [], "allow_users": [], "inherited": True},
        ),
        DiscoveredFile(
            path="//server/bauamt/plan.docx",
            file_hash="sha256:def456",
            size_bytes=120000,
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            last_modified="2025-02-15T08:00:00+00:00",
            acl={"source": "ntfs", "allow_groups": [], "deny_groups": [], "allow_users": [], "inherited": True},
        ),
    ]
    mock_discovery.discover.return_value = _make_result(files)

    response = client.post("/api/v1/local/discover", json={
        "source": "smb",
        "paths": ["//server/bauamt"],
    })
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["total_files"] == 2
    assert data["data"]["new_files"] == 2
    assert data["data"]["changed_files"] == 0
    assert data["data"]["unchanged_files"] == 0
    assert len(data["data"]["files"]) == 2
    assert data["data"]["files"][0]["path"] == "//server/bauamt/antrag.pdf"
    assert data["data"]["files"][0]["acl"]["source"] == "ntfs"
    assert data["request_id"]


def test_discover_with_hash_map(client, mock_discovery):
    """Files with matching hashes should be marked as unchanged."""
    files = [
        DiscoveredFile(
            path="//server/doc1.pdf",
            file_hash="sha256:same",
            size_bytes=1000,
            mime_type="application/pdf",
            last_modified="2025-01-01T00:00:00+00:00",
        ),
        DiscoveredFile(
            path="//server/doc2.pdf",
            file_hash="sha256:changed",
            size_bytes=2000,
            mime_type="application/pdf",
            last_modified="2025-02-01T00:00:00+00:00",
        ),
    ]
    since_map = {
        "//server/doc1.pdf": "sha256:same",
        "//server/doc2.pdf": "sha256:old",
    }
    mock_discovery.discover.return_value = _make_result(files, since_map)

    response = client.post("/api/v1/local/discover", json={
        "source": "smb",
        "paths": ["//server"],
        "since_hash_map": since_map,
    })
    data = response.json()
    assert data["success"] is True
    assert data["data"]["unchanged_files"] == 1
    assert data["data"]["changed_files"] == 1
    assert data["data"]["new_files"] == 0


def test_discover_r2_success(client, mock_discovery):
    files = [
        DiscoveredFile(
            path="tenant/uploads/report.pdf",
            file_hash="sha256:r2hash",
            size_bytes=50000,
            mime_type="application/pdf",
            last_modified="2025-03-10T12:00:00Z",
            acl={"source": "r2", "allow_groups": [], "deny_groups": [], "allow_users": [], "inherited": False},
        ),
    ]
    mock_discovery.discover.return_value = _make_result(files)

    response = client.post("/api/v1/local/discover", json={
        "source": "r2",
        "paths": ["tenant/uploads/"],
    })
    data = response.json()
    assert data["success"] is True
    assert data["data"]["total_files"] == 1
    assert data["data"]["files"][0]["acl"]["source"] == "r2"


def test_discover_smb_path_not_found(client, mock_discovery):
    mock_discovery.discover.side_effect = DiscoveryError(
        "Path not found: //server/missing", code="SMB_PATH_NOT_FOUND"
    )

    response = client.post("/api/v1/local/discover", json={
        "source": "smb",
        "paths": ["//server/missing"],
    })
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "SMB_PATH_NOT_FOUND"


def test_discover_smb_auth_failed(client, mock_discovery):
    mock_discovery.discover.side_effect = DiscoveryError(
        "Access denied", code="SMB_AUTH_FAILED"
    )

    response = client.post("/api/v1/local/discover", json={
        "source": "smb",
        "paths": ["//server/restricted"],
    })
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "SMB_AUTH_FAILED"


def test_discover_r2_connection_failed(client, mock_discovery):
    mock_discovery.discover.side_effect = DiscoveryError(
        "R2 connection failed", code="R2_CONNECTION_FAILED"
    )

    response = client.post("/api/v1/local/discover", json={
        "source": "r2",
        "paths": ["tenant/uploads/"],
    })
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "R2_CONNECTION_FAILED"


def test_discover_invalid_source(client):
    """Pydantic should reject invalid source values."""
    response = client.post("/api/v1/local/discover", json={
        "source": "ftp",
        "paths": ["/some/path"],
    })
    assert response.status_code == 422


def test_discover_empty_paths(client):
    """Pydantic should reject empty paths list."""
    response = client.post("/api/v1/local/discover", json={
        "source": "smb",
        "paths": [],
    })
    assert response.status_code == 422


def test_discover_request_id(client, mock_discovery):
    mock_discovery.discover.return_value = _make_result([])

    response = client.post(
        "/api/v1/local/discover",
        json={"source": "smb", "paths": ["//server/test"]},
        headers={"X-Request-ID": "disc-req-555"},
    )
    data = response.json()
    assert data["request_id"] == "disc-req-555"
    assert response.headers["X-Request-ID"] == "disc-req-555"


# ── SMBClient unit tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smb_client_discover_real_files():
    """Test SMB client against real temp files."""
    smb = SMBClient()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test files
        pdf_path = os.path.join(tmpdir, "test.pdf")
        txt_path = os.path.join(tmpdir, "readme.txt")
        skip_path = os.path.join(tmpdir, "image.png")  # Not a supported extension

        for p in [pdf_path, txt_path, skip_path]:
            with open(p, "wb") as f:
                f.write(b"test content for " + p.encode())

        files = await smb.discover([tmpdir])

        # Should find .pdf and .txt but not .png
        found_paths = {f.path for f in files}
        assert pdf_path in found_paths
        assert txt_path in found_paths
        assert skip_path not in found_paths

        # Check hash format
        for f in files:
            assert f.file_hash.startswith("sha256:")
            assert f.size_bytes > 0
            assert f.mime_type
            assert f.acl is not None


@pytest.mark.asyncio
async def test_smb_client_path_not_found():
    """Test SMB client raises error for missing paths."""
    smb = SMBClient()
    from app.services.discovery.smb_client import SMBError
    with pytest.raises(SMBError, match="Path not found"):
        await smb.discover(["/nonexistent/path/that/does/not/exist"])


# ── DiscoveryResult status assignment ────────────────────────────────


def test_discovery_result_status_assignment():
    """Test that DiscoveryResult correctly assigns new/changed/unchanged."""
    files = [
        DiscoveredFile(path="a.pdf", file_hash="sha256:aaa", size_bytes=100, mime_type="application/pdf", last_modified="2025-01-01"),
        DiscoveredFile(path="b.pdf", file_hash="sha256:bbb_new", size_bytes=200, mime_type="application/pdf", last_modified="2025-01-01"),
        DiscoveredFile(path="c.pdf", file_hash="sha256:ccc", size_bytes=300, mime_type="application/pdf", last_modified="2025-01-01"),
    ]
    since_map = {
        "b.pdf": "sha256:bbb_old",
        "c.pdf": "sha256:ccc",
    }

    result = DiscoveryResult(files=files, since_hash_map=since_map)
    assert result.new_files == 1       # a.pdf not in hash map
    assert result.changed_files == 1   # b.pdf hash differs
    assert result.unchanged_files == 1 # c.pdf hash matches
    assert result.total_files == 3

    statuses = {f.path: f.status for f in result.files}
    assert statuses["a.pdf"] == "new"
    assert statuses["b.pdf"] == "changed"
    assert statuses["c.pdf"] == "unchanged"
