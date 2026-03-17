import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Test client with auth disabled (no HMAC secret configured)."""
    app.state._test_mode = True
    with TestClient(app) as c:
        yield c
