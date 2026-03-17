"""Shared helpers for parse routers (local and online)."""

from app.models.common import ErrorCode
from app.services.parsing.models import ParseStatus


def map_parse_error(error_msg: str | None) -> str:
    error_lower = (error_msg or "").lower()
    if "encrypt" in error_lower or "password" in error_lower:
        return ErrorCode.PARSE_ENCRYPTED
    if "corrupt" in error_lower or "damaged" in error_lower:
        return ErrorCode.PARSE_CORRUPTED
    if "timeout" in error_lower:
        return ErrorCode.PARSE_TIMEOUT
    return ErrorCode.PARSE_FAILED


def check_parse_failure(result, request_id: str) -> dict | None:
    """Return an error dict if the result indicates failure, else None."""
    if result.status == ParseStatus.UNSUPPORTED:
        return {
            "success": False,
            "error": ErrorCode.PARSE_UNSUPPORTED_FORMAT,
            "detail": result.error,
            "request_id": request_id,
        }

    if result.status == ParseStatus.FAILED:
        return {
            "success": False,
            "error": map_parse_error(result.error),
            "detail": result.error,
            "request_id": request_id,
        }

    content = result.text or ""
    if not content.strip():
        return {
            "success": False,
            "error": ErrorCode.PARSE_EMPTY,
            "detail": "Document contained no extractable text",
            "request_id": request_id,
        }

    return None
