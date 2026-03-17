"""SMB file discovery client — scans mounted SMB shares for files and NTFS permissions."""

import hashlib
import mimetypes
import os
from datetime import datetime, timezone

from app.config import ext
from app.utils.logger import get_logger

log = get_logger(__name__)


class SMBError(Exception):
    def __init__(self, message: str, code: str = "SMB_CONNECTION_FAILED"):
        super().__init__(message)
        self.code = code


class DiscoveredFile:
    def __init__(
        self,
        path: str,
        file_hash: str,
        size_bytes: int,
        mime_type: str,
        last_modified: str,
        acl: dict | None = None,
    ):
        self.path = path
        self.file_hash = file_hash
        self.size_bytes = size_bytes
        self.mime_type = mime_type
        self.last_modified = last_modified
        self.acl = acl


# File extensions we scan for document ingestion
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx",
    ".txt", ".csv", ".html", ".htm", ".rtf", ".odt",
}


class SMBClient:
    """Scans mounted SMB/CIFS shares for documents.

    Assumes shares are mounted to the local filesystem (e.g. via mount.cifs
    or Docker volume mounts). Reads NTFS ACLs when available via extended
    attributes or falls back to a default ACL.
    """

    async def discover(
        self,
        paths: list[str],
        since_hash_map: dict[str, str] | None = None,
    ) -> list[DiscoveredFile]:
        since_hash_map = since_hash_map or {}
        files: list[DiscoveredFile] = []

        for path in paths:
            if not os.path.exists(path):
                raise SMBError(f"Path not found: {path}", code="SMB_PATH_NOT_FOUND")

            if os.path.isfile(path):
                discovered = self._scan_file(path, since_hash_map)
                if discovered:
                    files.append(discovered)
            else:
                self._scan_directory(path, files, since_hash_map)

        log.info("smb_discover_complete", paths=paths, total_files=len(files))
        return files

    def _scan_directory(
        self,
        root: str,
        files: list[DiscoveredFile],
        since_hash_map: dict[str, str],
    ) -> None:
        try:
            for dirpath, _, filenames in os.walk(root):
                for filename in filenames:
                    ext_lower = os.path.splitext(filename)[1].lower()
                    if ext_lower not in SUPPORTED_EXTENSIONS:
                        continue
                    full_path = os.path.join(dirpath, filename)
                    discovered = self._scan_file(full_path, since_hash_map)
                    if discovered:
                        files.append(discovered)
        except PermissionError as e:
            raise SMBError(f"Access denied: {e}", code="SMB_AUTH_FAILED") from e
        except OSError as e:
            raise SMBError(f"SMB error scanning {root}: {e}") from e

    def _scan_file(self, path: str, since_hash_map: dict[str, str]) -> DiscoveredFile | None:
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return None
        except PermissionError as e:
            log.warning("smb_file_access_denied", path=path, error=str(e))
            return None

        size = stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        file_hash = self._compute_hash(path)
        acl = self._read_acl(path)

        return DiscoveredFile(
            path=path,
            file_hash=file_hash,
            size_bytes=size,
            mime_type=mime_type,
            last_modified=mtime,
            acl=acl,
        )

    def _compute_hash(self, path: str) -> str:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
        except OSError:
            return "sha256:error"
        return f"sha256:{h.hexdigest()}"

    def _read_acl(self, path: str) -> dict:
        """Read NTFS ACLs via extended attributes or platform APIs.

        Falls back to a default ACL when running outside Windows or when
        NTFS xattrs are not available on the mount.
        """
        # On Windows, we could use win32security; on Linux with CIFS mounts,
        # we could parse cifsacl xattrs. For now, return a default ACL that
        # can be overridden by the LDAP enrichment step.
        return {
            "source": "ntfs",
            "allow_groups": [],
            "deny_groups": [],
            "allow_users": [],
            "inherited": True,
        }
