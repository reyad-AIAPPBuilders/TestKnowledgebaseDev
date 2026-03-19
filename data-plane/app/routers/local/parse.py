"""
POST /api/v1/local/document-parse        — Parse a document from SMB or R2 source.
POST /api/v1/local/document-parse/upload — Parse an uploaded document file.
"""

import os
import tempfile

from fastapi import APIRouter, File, Request, UploadFile

from app.models.common import ErrorCode, ResponseEnvelope
from app.models.local.parse import LocalParseData, LocalParseRequest
from app.routers._parse_utils import check_parse_failure
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/local", tags=["Local - Document Parsing"])


@router.post(
    "/document-parse",
    summary="Parse a local document",
    description="Extract text, tables, and metadata from a document on SMB or R2.\n\n"
    "**Sources:**\n"
    "- **smb**: Reads from a mounted file share path\n"
    "- **r2**: Downloads via pre-signed URL from Cloudflare R2, then parses\n\n"
    "**Supported formats:** PDF, DOCX, DOC, PPTX, ODT, XLSX, XLS, TXT, CSV, HTML, RTF.\n\n"
    "**Error codes:** `PARSE_FAILED`, `PARSE_ENCRYPTED`, `PARSE_CORRUPTED`, `PARSE_EMPTY`, "
    "`PARSE_TIMEOUT`, `PARSE_UNSUPPORTED_FORMAT`, `R2_FILE_NOT_FOUND`",
    response_description="Extracted text content with page count, language, and table count",
)
async def parse_local(body: LocalParseRequest, request: Request) -> ResponseEnvelope[LocalParseData]:
    request_id = request.state.request_id
    parser = request.app.state.parser

    if body.source == "r2":
        if not body.r2_presigned_url:
            return ResponseEnvelope(
                success=False,
                error=ErrorCode.R2_FILE_NOT_FOUND,
                detail="r2_presigned_url is required when source is r2",
                request_id=request_id,
            )
        result = await parser.parse_from_url(
            url=body.r2_presigned_url,
            mime_type=body.mime_type,
        )
    else:
        # SMB source — file_path is a local/mounted path
        result = await parser.parse_from_file(
            file_path=body.file_path,
            mime_type=body.mime_type,
            filename=body.file_path.rsplit("/", 1)[-1] if "/" in body.file_path else body.file_path,
        )

    return _build_response(result, body.file_path, request_id)


@router.post(
    "/document-parse/upload",
    summary="Parse an uploaded document",
    description="Upload a document file directly and extract text, tables, and metadata.\n\n"
    "**Content-Type:** `multipart/form-data` — send the raw binary file in the `file` form field.\n"
    "Do **not** base64-encode the file; send the original binary document as-is.\n\n"
    "**Supported formats:** PDF, DOCX, DOC, PPTX, ODT, XLSX, XLS, TXT, CSV, HTML, RTF.\n\n"
    "**Example (cURL):**\n"
    "```\n"
    "curl -X POST /api/v1/local/document-parse/upload -F \"file=@report.pdf\"\n"
    "```\n\n"
    "**Error codes:** `PARSE_FAILED`, `PARSE_ENCRYPTED`, `PARSE_CORRUPTED`, `PARSE_EMPTY`, "
    "`PARSE_TIMEOUT`, `PARSE_UNSUPPORTED_FORMAT`",
    response_description="Extracted text content with page count, language, and table count",
)
async def parse_upload(request: Request, file: UploadFile = File(...)) -> ResponseEnvelope[LocalParseData]:
    request_id = request.state.request_id
    parser = request.app.state.parser

    # Save upload to temp file
    suffix = os.path.splitext(file.filename or "")[1] or ""
    fd, temp_path = tempfile.mkstemp(suffix=suffix)
    try:
        content = await file.read()
        with os.fdopen(fd, "wb") as f:
            f.write(content)

        result = await parser.parse_from_file(
            file_path=temp_path,
            mime_type=file.content_type,
            filename=file.filename,
        )

        return _build_response(result, file.filename or "upload", request_id)

    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _build_response(result, file_path: str, request_id: str) -> ResponseEnvelope[LocalParseData]:
    """Convert a ParseResult into the standard API response."""
    error = check_parse_failure(result, request_id)
    if error:
        return ResponseEnvelope(**error)

    content = result.text or ""
    return ResponseEnvelope(
        success=True,
        data=LocalParseData(
            file_path=file_path,
            content=content,
            pages=result.pages_parsed,
            language=result.metadata.language,
            extracted_tables=len(result.tables),
            content_length=len(content),
        ),
        request_id=request_id,
    )
