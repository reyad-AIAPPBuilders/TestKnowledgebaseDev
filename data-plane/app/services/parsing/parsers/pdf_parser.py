"""PDF parser using PyMuPDF (fitz)."""

import pymupdf

from app.services.parsing.models import DocumentMetadata, DocumentType, ParseOptions, TableData
from app.services.parsing.parsers.base import BaseParser, ParsedContent
from app.utils.logger import get_logger

log = get_logger(__name__)


class PdfParser(BaseParser):
    """Extract text from PDF files using PyMuPDF."""

    def supports(self) -> list[str]:
        return [DocumentType.PDF.value]

    async def parse(self, file_path: str, options: ParseOptions) -> ParsedContent:
        doc = pymupdf.open(file_path)
        try:
            return self._extract(doc, options)
        finally:
            doc.close()

    def _extract(self, doc: pymupdf.Document, options: ParseOptions) -> ParsedContent:
        pages: list[str] = []
        tables: list[TableData] = []
        pages_parsed = 0
        pages_failed = 0

        max_pages = options.max_pages if options.max_pages is not None else len(doc)
        page_limit = min(max_pages, len(doc))

        for page_num in range(page_limit):
            try:
                page = doc[page_num]
                text = page.get_text("text")

                if not text.strip() and options.ocr_enabled:
                    text = self._ocr_page(page, options)

                if text.strip():
                    pages.append(text.strip())
                    pages_parsed += 1

                if options.extract_tables:
                    page_tables = self._extract_tables(page, page_num)
                    tables.extend(page_tables)

            except Exception as e:
                log.warning("page_parse_failed", page=page_num, error=str(e))
                pages_failed += 1

        full_text = options.page_separator.join(pages) if pages else ""

        meta_raw = doc.metadata or {}
        metadata = DocumentMetadata(
            title=meta_raw.get("title") or None,
            author=meta_raw.get("author") or None,
            subject=meta_raw.get("subject") or None,
            creator=meta_raw.get("creator") or None,
            creation_date=meta_raw.get("creationDate") or None,
            modification_date=meta_raw.get("modDate") or None,
            page_count=len(doc),
            word_count=len(full_text.split()) if full_text else 0,
            char_count=len(full_text),
        )

        return ParsedContent(
            text=full_text,
            metadata=metadata,
            tables=tables,
            pages_parsed=pages_parsed,
            pages_failed=pages_failed,
        )

    def _ocr_page(self, page: pymupdf.Page, options: ParseOptions) -> str:
        """Attempt OCR on a page using PyMuPDF's built-in Tesseract integration."""
        try:
            tp = page.get_textpage_ocr(language=options.ocr_language, full=True)
            return page.get_text("text", textpage=tp)
        except Exception as e:
            log.debug("ocr_failed", error=str(e))
            try:
                return page.get_text("text")
            except Exception:
                return ""

    def _extract_tables(self, page: pymupdf.Page, page_num: int) -> list[TableData]:
        """Extract tables from a PDF page using PyMuPDF's table finder."""
        result: list[TableData] = []
        try:
            tables = page.find_tables()
            for table in tables:
                extracted = table.extract()
                if not extracted or len(extracted) < 2:
                    continue

                headers = [str(cell) if cell else "" for cell in extracted[0]]
                rows = [
                    [str(cell) if cell else "" for cell in row]
                    for row in extracted[1:]
                ]

                result.append(TableData(page=page_num, headers=headers, rows=rows))
        except Exception as e:
            log.debug("table_extraction_failed", page=page_num, error=str(e))

        return result
