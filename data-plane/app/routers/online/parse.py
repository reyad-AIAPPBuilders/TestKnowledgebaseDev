"""
POST /api/v1/online/parse — Parse a document from a public URL.
"""

from fastapi import APIRouter, Request

from app.models.common import ResponseEnvelope
from app.models.online.parse import OnlineParseData, OnlineParseRequest
from app.routers._parse_utils import check_parse_failure
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/online", tags=["Online - Document Parsing"])


@router.post(
    "/parse",
    summary="Parse a document from URL",
    description="Download and extract text, tables, and metadata from a document at a public URL.\n\n"
    "**Supported formats:** PDF, DOCX, DOC, PPTX, ODT, XLSX, XLS, TXT, CSV, HTML, RTF.\n\n"
    "**Optional X-API-Key header** — required only when `DP_ONLINE_API_KEYS` is configured.\n\n"
    "**Error codes:** `PARSE_FAILED`, `PARSE_ENCRYPTED`, `PARSE_CORRUPTED`, `PARSE_EMPTY`, "
    "`PARSE_TIMEOUT`, `PARSE_UNSUPPORTED_FORMAT`",
    response_description="Extracted text content with page count, language, and table count",
)
async def parse_online(body: OnlineParseRequest, request: Request) -> ResponseEnvelope[OnlineParseData]:
    request_id = request.state.request_id
    parser = request.app.state.parser

    result = await parser.parse_from_url(
        url=body.url,
        mime_type=body.mime_type,
    )

    error = check_parse_failure(result, request_id)
    if error:
        return ResponseEnvelope(**error)

    content = result.text or ""
    return ResponseEnvelope(
        success=True,
        data=OnlineParseData(
            url=body.url,
            content=content,
            pages=result.pages_parsed,
            language=result.metadata.language,
            extracted_tables=len(result.tables),
            content_length=len(content),
        ),
        request_id=request_id,
    )
