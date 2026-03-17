"""
POST /api/v1/local/discover — Scan file sources, read permissions, compute hashes, return what changed.
"""

from fastapi import APIRouter, Request

from app.models.common import ErrorCode, NtfsACL, ResponseEnvelope
from app.models.local.discover import DiscoverData, DiscoverRequest, FileInfo
from app.services.discovery.discovery_service import DiscoveryError
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/local", tags=["Local - File Discovery"])

# Map discovery error codes to ErrorCode enum values
_ERROR_CODE_MAP = {
    "SMB_CONNECTION_FAILED": ErrorCode.SMB_CONNECTION_FAILED,
    "SMB_AUTH_FAILED": ErrorCode.SMB_AUTH_FAILED,
    "SMB_PATH_NOT_FOUND": ErrorCode.SMB_PATH_NOT_FOUND,
    "R2_CONNECTION_FAILED": ErrorCode.R2_CONNECTION_FAILED,
    "R2_FILE_NOT_FOUND": ErrorCode.R2_FILE_NOT_FOUND,
    "LDAP_CONNECTION_FAILED": ErrorCode.LDAP_CONNECTION_FAILED,
    "LDAP_AUTH_FAILED": ErrorCode.LDAP_AUTH_FAILED,
    "VALIDATION_PATH_OUTSIDE_ROOTS": ErrorCode.VALIDATION_PATH_OUTSIDE_ROOTS,
}


@router.post(
    "/discover",
    summary="Scan file sources for changes",
    description="Scans SMB file shares or Cloudflare R2 buckets for documents. Reads NTFS permissions, computes SHA-256 hashes, and classifies each file as `new`, `changed`, or `unchanged` based on the `since_hash_map`.\n\nThis is the **first step** in every local ingestion pipeline. It does NOT parse or embed — use `/local/parse` and `/local/ingest` for that.\n\n**Supported sources:**\n- `smb`: Mounted CIFS/SMB file shares (scans recursively for PDF, DOCX, XLSX, TXT, CSV, HTML, RTF, ODT)\n- `r2`: Cloudflare R2 / S3-compatible object storage (lists objects by prefix)\n\n**Error codes:** `SMB_CONNECTION_FAILED`, `SMB_AUTH_FAILED`, `SMB_PATH_NOT_FOUND`, `R2_CONNECTION_FAILED`, `R2_FILE_NOT_FOUND`, `LDAP_CONNECTION_FAILED`, `VALIDATION_PATH_OUTSIDE_ROOTS`",
    response_description="List of discovered files with hashes, sizes, MIME types, and NTFS permissions",
)
async def discover(body: DiscoverRequest, request: Request) -> ResponseEnvelope[DiscoverData]:
    request_id = request.state.request_id
    discovery = request.app.state.discovery

    try:
        result = await discovery.discover(
            source=body.source,
            paths=body.paths,
            since_hash_map=body.since_hash_map,
        )
    except DiscoveryError as e:
        error_code = _ERROR_CODE_MAP.get(e.code, ErrorCode.SMB_CONNECTION_FAILED)
        log.error("discover_failed", source=body.source, error=str(e), code=e.code)
        return ResponseEnvelope(
            success=False,
            error=error_code,
            detail=str(e),
            request_id=request_id,
        )

    file_infos = []
    for f in result.files:
        acl = None
        if f.acl:
            acl = NtfsACL(
                source=f.acl.get("source", "ntfs"),
                allow_groups=f.acl.get("allow_groups", []),
                deny_groups=f.acl.get("deny_groups", []),
                allow_users=f.acl.get("allow_users", []),
                inherited=f.acl.get("inherited", True),
            )
        file_infos.append(FileInfo(
            path=f.path,
            file_hash=f.file_hash,
            size_bytes=f.size_bytes,
            mime_type=f.mime_type,
            last_modified=f.last_modified,
            status=getattr(f, "status", "new"),
            acl=acl,
        ))

    return ResponseEnvelope(
        success=True,
        data=DiscoverData(
            total_files=result.total_files,
            new_files=result.new_files,
            changed_files=result.changed_files,
            unchanged_files=result.unchanged_files,
            files=file_infos,
        ),
        request_id=request_id,
    )
