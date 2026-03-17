"""Main parser orchestrator — dispatches to the correct parser based on document type.

Parser selection strategy:
- Cloud mode (LLAMA_CLOUD_API_KEY set): LlamaParse for PDF/DOCX/DOC/PPTX/ODT
- Local mode (no API key):             PyMuPDF for PDF, python-docx for DOCX (lightweight, no heavy deps)
- Always:                               SpreadsheetParser for XLSX/XLS, TextParser for TXT/CSV/HTML/RTF
"""

import os
import time

import httpx

from app.config import ext
from app.services.parsing.detector import detect_document_type
from app.services.parsing.downloader import DownloadError, cleanup_file, download_document
from app.services.parsing.models import (
    DocumentType,
    ParseOptions,
    ParseResult,
    ParseStatus,
)
from app.services.parsing.parsers.base import BaseParser
from app.services.parsing.parsers.spreadsheet_parser import SpreadsheetParser
from app.services.parsing.parsers.text_parser import TextParser
from app.utils.logger import get_logger

log = get_logger(__name__)


class ParserService:
    """Orchestrates document parsing — download, detect type, parse, return."""

    def __init__(self) -> None:
        self._parsers: dict[str, BaseParser] = {}
        self._http_client: httpx.AsyncClient | None = None
        self._llama_parser = None
        self._use_llama = bool(ext.llama_cloud_api_key)

    async def startup(self) -> None:
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; KI2-DataPlane/1.0)"},
        )

        # Register format-specific parsers (always available)
        for parser in [SpreadsheetParser(), TextParser()]:
            for doc_type in parser.supports():
                self._parsers[doc_type] = parser

        # Register document parser based on mode
        if self._use_llama:
            from app.services.parsing.parsers.llama_parser import LlamaParser

            llama = LlamaParser()
            await llama.startup()
            self._llama_parser = llama
            for doc_type in llama.supports():
                self._parsers[doc_type] = llama
            log.info("parser_service_started", backend="llamaparse", supported_types=list(self._parsers.keys()))
        else:
            from app.services.parsing.parsers.docx_parser import DocxParser
            from app.services.parsing.parsers.pdf_parser import PdfParser

            pdf_parser = PdfParser()
            docx_parser = DocxParser()
            for doc_type in pdf_parser.supports():
                self._parsers[doc_type] = pdf_parser
            for doc_type in docx_parser.supports():
                self._parsers[doc_type] = docx_parser
            log.info("parser_service_started", backend="local", supported_types=list(self._parsers.keys()))

    async def shutdown(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
        if self._llama_parser:
            await self._llama_parser.shutdown()
        log.info("parser_service_stopped")

    async def check_health(self) -> bool:
        """Returns True if the parser service is operational."""
        if self._use_llama and self._llama_parser:
            return await self._llama_parser.check_health()
        # Unstructured is always available (local library)
        return True

    @property
    def supported_types(self) -> list[str]:
        return list(set(self._parsers.keys()))

    @property
    def parser_backend(self) -> str:
        return "llamaparse" if self._use_llama else "local"

    async def parse_from_url(self, url: str, mime_type: str | None = None) -> ParseResult:
        """Download a document from URL, detect type, parse, and return result."""
        start = time.monotonic()
        file_path: str | None = None

        try:
            download_result = await download_document(url, self._http_client)
            file_path = download_result.file_path

            doc_type = self._detect_type_from_file(
                file_path=file_path,
                content_type=mime_type or download_result.content_type,
                filename=download_result.filename,
                url=url,
            )

            result = await self._parse_file(
                file_path=file_path,
                doc_type=doc_type,
                options=ParseOptions(),
            )

            self._enrich_result(
                result,
                source_url=url,
                filename=download_result.filename,
                file_size=download_result.file_size,
                mime_type=download_result.content_type,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

            return result

        except DownloadError as e:
            duration = int((time.monotonic() - start) * 1000)
            log.error("parse_url_download_failed", url=url, error=str(e))
            return ParseResult(
                status=ParseStatus.FAILED,
                document_type=DocumentType.UNKNOWN,
                source_url=url,
                error=f"Download failed: {e}",
                duration_ms=duration,
            )

        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            log.error("parse_url_failed", url=url, error=str(e))
            return ParseResult(
                status=ParseStatus.FAILED,
                document_type=DocumentType.UNKNOWN,
                source_url=url,
                error=str(e),
                duration_ms=duration,
            )

        finally:
            if file_path:
                cleanup_file(file_path)

    async def parse_from_file(
        self,
        file_path: str,
        mime_type: str | None = None,
        filename: str | None = None,
    ) -> ParseResult:
        """Parse a local file (e.g. downloaded from SMB or R2)."""
        start = time.monotonic()

        try:
            doc_type = self._detect_type_from_file(
                file_path=file_path,
                content_type=mime_type,
                filename=filename or os.path.basename(file_path),
            )

            result = await self._parse_file(
                file_path=file_path,
                doc_type=doc_type,
                options=ParseOptions(),
            )

            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            self._enrich_result(
                result,
                filename=filename or os.path.basename(file_path),
                file_size=file_size,
                mime_type=mime_type,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

            return result

        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            log.error("parse_file_failed", file_path=file_path, error=str(e))
            return ParseResult(
                status=ParseStatus.FAILED,
                document_type=DocumentType.UNKNOWN,
                filename=filename,
                error=str(e),
                duration_ms=duration,
            )

    def _detect_type_from_file(
        self,
        file_path: str,
        content_type: str | None = None,
        filename: str | None = None,
        url: str | None = None,
    ) -> DocumentType:
        with open(file_path, "rb") as f:
            magic = f.read(4096)

        return detect_document_type(
            content_type=content_type,
            filename=filename,
            url=url,
            data=magic,
        )

    def _enrich_result(
        self,
        result: ParseResult,
        *,
        source_url: str | None = None,
        filename: str | None = None,
        file_size: int | None = None,
        mime_type: str | None = None,
        duration_ms: int | None = None,
    ) -> ParseResult:
        if source_url is not None:
            result.source_url = source_url
        if filename is not None:
            result.filename = filename
        if file_size is not None:
            result.metadata.file_size_bytes = file_size
        if mime_type is not None:
            result.metadata.mime_type = mime_type
        if duration_ms is not None:
            result.duration_ms = duration_ms
        return result

    async def _parse_file(
        self,
        file_path: str,
        doc_type: DocumentType,
        options: ParseOptions,
    ) -> ParseResult:
        parser = self._parsers.get(doc_type.value)
        if not parser:
            return ParseResult(
                status=ParseStatus.UNSUPPORTED,
                document_type=doc_type,
                error=f"Unsupported document type: {doc_type.value}",
            )

        try:
            content = await parser.parse(file_path, options)

            status = ParseStatus.SUCCESS
            if content.pages_failed and content.pages_failed > 0:
                if content.pages_parsed and content.pages_parsed > 0:
                    status = ParseStatus.PARTIAL
                else:
                    status = ParseStatus.FAILED

            return ParseResult(
                status=status,
                document_type=doc_type,
                text=content.text,
                metadata=content.metadata,
                tables=content.tables,
                pages_parsed=content.pages_parsed,
                pages_failed=content.pages_failed,
            )

        except Exception as e:
            log.error("parser_execution_failed", doc_type=doc_type.value, backend=self.parser_backend, error=str(e))
            return ParseResult(
                status=ParseStatus.FAILED,
                document_type=doc_type,
                error=f"Parser error ({self.parser_backend}): {e}",
            )
