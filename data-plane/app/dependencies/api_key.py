"""API key authentication dependency for online endpoints."""

from fastapi import Header, HTTPException

from app.config import settings


def require_api_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> str | None:
    """Validate the API key sent in the X-API-Key header.

    If no API keys are configured (DP_ONLINE_API_KEYS is empty), access is allowed without a key.
    If keys are configured, a valid X-API-Key header is required.
    """
    valid_keys = [k.strip() for k in settings.online_api_keys.split(",") if k.strip()]
    if not valid_keys:
        # No keys configured — online endpoints are open
        return None
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header is required")
    if x_api_key not in valid_keys:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key
