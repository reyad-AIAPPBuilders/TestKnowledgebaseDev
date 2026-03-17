import asyncio
import functools
from urllib.parse import urlparse

from app.config import ext
from app.utils.logger import get_logger

log = get_logger(__name__)


class AuditLogger:
    """ClickHouse audit logger for Data Plane events."""

    def __init__(self) -> None:
        self._client = None

    async def start(self) -> None:
        try:
            from clickhouse_driver import Client

            loop = asyncio.get_running_loop()
            self._client = await loop.run_in_executor(
                None,
                functools.partial(
                    Client,
                    host=ext.clickhouse_host,
                    port=ext.clickhouse_port,
                    database=ext.clickhouse_db,
                    user=ext.clickhouse_user,
                    password=ext.clickhouse_password,
                ),
            )
            log.info("audit_logger_started", host=ext.clickhouse_host)
            ok = await self.check_health()
            if not ok and ext.clickhouse_required:
                raise RuntimeError("ClickHouse health check failed")
        except Exception as exc:
            log.warning("audit_logger_init_failed", error=str(exc))
            self._client = None
            if ext.clickhouse_required:
                raise

    async def close(self) -> None:
        if self._client:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._client.disconnect)
            except Exception:
                pass
            self._client = None

    async def check_health(self) -> bool:
        if not self._client:
            return False
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self._client.execute, "SELECT 1")
            return bool(result)
        except Exception:
            return False

    async def log(
        self,
        action: str,
        actor: str,
        url: str,
        status: str = "success",
        request_id: str = "",
        api_key_hash: str = "",
        **details: int | str,
    ) -> None:
        if not self._client:
            log.debug("audit_skipped_no_client", action=action, url=url)
            return

        row = {
            "action": action,
            "actor": actor,
            "url": url,
            "domain": urlparse(url).netloc,
            "status": status,
            "documents_found": int(details.get("documents_found", 0)),
            "word_count": int(details.get("word_count", 0)),
            "duration_ms": int(details.get("duration_ms", 0)),
            "error": str(details.get("error", "")),
            "request_id": request_id,
            "api_key_hash": api_key_hash,
        }

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._insert, row)
        except Exception as exc:
            log.warning("audit_log_failed", action=action, url=url, error=str(exc))

    def _insert(self, row: dict) -> None:
        self._client.execute(  # type: ignore[union-attr]
            "INSERT INTO audit_log "
            "(action, actor, url, domain, status, documents_found, "
            "word_count, duration_ms, error, request_id, api_key_hash) VALUES",
            [row],
        )
