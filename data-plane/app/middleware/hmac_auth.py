import json

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.config import settings
from app.utils.hmac import verify_signature
from app.utils.logger import get_logger

log = get_logger(__name__)

PUBLIC_PATHS = {
    "/api/v1/health",
    "/api/v1/ready",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/redoc",
}


def _error_response(error_code: str, detail: str, status_code: int) -> Response:
    body = json.dumps({
        "success": False,
        "error": error_code,
        "detail": detail,
        "request_id": "",
    })
    return Response(content=body, status_code=status_code, media_type="application/json")


class HMACAuthMiddleware(BaseHTTPMiddleware):
    """Validate HMAC-SHA256 signature from X-Signature + X-Timestamp headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Auth disabled when no secret is configured
        if not settings.hmac_secret:
            return await call_next(request)

        # CORS preflight
        if request.method.upper() == "OPTIONS":
            return await call_next(request)

        # Public paths — no auth required
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        signature = request.headers.get("X-Signature")
        timestamp = request.headers.get("X-Timestamp")

        if not signature or not timestamp:
            log.warning(
                "hmac_missing_headers",
                path=request.url.path,
                client=request.client.host if request.client else "unknown",
            )
            return _error_response("AUTH_MISSING", "Missing X-Signature and X-Timestamp headers", 401)

        # Read body for signature verification
        body = await request.body()

        is_valid, error_msg = verify_signature(
            secret=settings.hmac_secret,
            method=request.method,
            path=request.url.path,
            timestamp=timestamp,
            body=body,
            signature=signature,
            max_age=settings.hmac_max_age,
        )

        if not is_valid:
            log.warning("hmac_auth_failed", path=request.url.path, reason=error_msg)
            error_code = "AUTH_EXPIRED" if "expired" in (error_msg or "").lower() else "AUTH_INVALID"
            return _error_response(error_code, error_msg or "Authentication failed", 403)

        return await call_next(request)
