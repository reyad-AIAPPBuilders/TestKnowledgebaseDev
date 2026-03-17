"""Discovery service — orchestrates file discovery across SMB, R2, and URL sources."""

from app.services.discovery.r2_client import R2Client, R2Error
from app.services.discovery.smb_client import DiscoveredFile, SMBClient, SMBError
from app.utils.logger import get_logger

log = get_logger(__name__)


class DiscoveryError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class DiscoveryResult:
    def __init__(
        self,
        files: list[DiscoveredFile],
        since_hash_map: dict[str, str],
    ):
        self.files = files
        self.total_files = len(files)
        self.new_files = 0
        self.changed_files = 0
        self.unchanged_files = 0

        for f in files:
            old_hash = since_hash_map.get(f.path)
            if old_hash is None:
                f.status = "new"
                self.new_files += 1
            elif old_hash != f.file_hash:
                f.status = "changed"
                self.changed_files += 1
            else:
                f.status = "unchanged"
                self.unchanged_files += 1


class DiscoveryService:
    """Orchestrates file discovery from multiple sources."""

    def __init__(self, smb_client: SMBClient, r2_client: R2Client) -> None:
        self._smb = smb_client
        self._r2 = r2_client

    async def discover(
        self,
        source: str,
        paths: list[str],
        since_hash_map: dict[str, str] | None = None,
    ) -> DiscoveryResult:
        since_hash_map = since_hash_map or {}

        if source == "smb":
            files = await self._discover_smb(paths, since_hash_map)
        elif source == "r2":
            files = await self._discover_r2(paths, since_hash_map)
        elif source == "url":
            files = await self._discover_url(paths, since_hash_map)
        else:
            raise DiscoveryError(f"Unknown source: {source}", code="VALIDATION_PATH_OUTSIDE_ROOTS")

        result = DiscoveryResult(files, since_hash_map)

        log.info(
            "discovery_complete",
            source=source,
            total=result.total_files,
            new=result.new_files,
            changed=result.changed_files,
            unchanged=result.unchanged_files,
        )

        return result

    async def _discover_smb(
        self, paths: list[str], since_hash_map: dict[str, str],
    ) -> list[DiscoveredFile]:
        try:
            return await self._smb.discover(paths, since_hash_map)
        except SMBError as e:
            raise DiscoveryError(str(e), code=e.code) from e

    async def _discover_r2(
        self, paths: list[str], since_hash_map: dict[str, str],
    ) -> list[DiscoveredFile]:
        try:
            return await self._r2.discover(paths, since_hash_map)
        except R2Error as e:
            raise DiscoveryError(str(e), code=e.code) from e

    async def _discover_url(
        self, paths: list[str], since_hash_map: dict[str, str],
    ) -> list[DiscoveredFile]:
        """URL-based discovery is a placeholder — URLs are handled by the scraper."""
        raise DiscoveryError(
            "URL discovery should use /crawl endpoint instead",
            code="VALIDATION_PATH_OUTSIDE_ROOTS",
        )
