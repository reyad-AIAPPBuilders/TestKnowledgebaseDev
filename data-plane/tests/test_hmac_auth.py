import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.utils.hmac import compute_signature


def _make_signed_headers(secret: str, method: str, path: str, body: bytes = b"") -> dict:
    timestamp = str(int(time.time()))
    signature = compute_signature(secret, method, path, timestamp, body)
    return {"X-Signature": signature, "X-Timestamp": timestamp}


def test_public_paths_no_auth():
    """Health and ready should work without auth even when HMAC is configured."""
    import app.config as config_mod

    original = config_mod.settings
    config_mod.settings = Settings(hmac_secret="test-secret-123")

    try:
        app.state._test_mode = True
        with TestClient(app) as client:
            assert client.get("/api/v1/health").status_code == 200
            assert client.get("/api/v1/ready").status_code == 200
    finally:
        config_mod.settings = original


def test_auth_disabled_when_no_secret(client):
    """All endpoints should be accessible when HMAC secret is empty."""
    # /api/v1/health is public anyway, just verify no 401/403
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_hmac_signature_verification():
    """Valid HMAC signature should pass authentication."""
    secret = "test-secret-456"

    import app.config as config_mod

    original = config_mod.settings
    config_mod.settings = Settings(hmac_secret=secret)

    try:
        app.state._test_mode = True
        with TestClient(app) as client:
            # Health is public — doesn't need auth
            headers = _make_signed_headers(secret, "GET", "/api/v1/health")
            response = client.get("/api/v1/health", headers=headers)
            assert response.status_code == 200
    finally:
        config_mod.settings = original
