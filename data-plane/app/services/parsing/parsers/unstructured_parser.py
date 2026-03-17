"""Document parser using the unstructured library (local, no API needed).

Fallback parser for local/on-premise deployments where LlamaParse cloud API
is not available. Runs entirely locally using the unstructured library.
"""

from unstructured.partition.auto import partition

from app.services.parsing.models import DocumentMetadata, DocumentType, ParseOptions, TableData
from app.services.parsing.parsers.base import BaseParser, ParsedContent
from app.utils.logger import get_logger

log = get_logger(__name__)


class UnstructuredParser(BaseParser):
    """Parse documents locally using the unstructured library."""

    def supports(self) -> list[str]:
        return [
            DocumentType.PDF.value,
            DocumentType.DOCX.value,
            DocumentType.DOC.value,
            DocumentType.PPTX.value,
            DocumentType.ODT.value,
            DocumentType.RTF.value,
            DocumentType.HTML.value,
        ]

    async def parse(self, file_path: str, options: ParseOptions) -> ParsedContent:
        log.info("unstructured_parse_start", file=file_path)

        kwargs: dict = {"filename": file_path}

        # For PDFs, configure OCR and language
        if file_path.lower().endswith(".pdf"):
            languages = options.ocr_language.replace("+", ",").split(",") if options.ocr_language else ["eng"]
            # Map short codes to unstructured language names
            lang_map = {"deu": "deu", "eng": "eng", "de": "deu", "en": "eng"}
            kwargs["languages"] = [lang_map.get(lang.strip(), lang.strip()) for lang in languages]

            if not options.ocr_enabled:
                kwargs["strategy"] = "fast"

        elements = partition(**kwargs)

        text_parts: list[str] = []
        tables: list[TableData] = []
        page_numbers: set[int] = set()

        for element in elements:
            element_type = type(element).__name__
            text = str(element).strip()

            if not text:
                continue

            # Track page numbers
            meta = getattr(element, "metadata", None)
            if meta:
                page_num = getattr(meta, "page_number", None)
                if page_num is not None:
                    page_numbers.add(page_num)

            # Handle tables
            if element_type == "Table":
                html_table = getattr(meta, "text_as_html", None) if meta else None
                if html_table:
                    table_data = _parse_html_table(html_table)
                    if table_data:
                        page = getattr(meta, "page_number", None) if meta else None
                        tables.append(TableData(
                            page=page,
                            headers=table_data[0] if table_data else [],
                            rows=table_data[1:] if len(table_data) > 1 else [],
                        ))

            # Convert headings to markdown
            if element_type == "Title":
                text_parts.append(f"# {text}")
            elif element_type == "Header":
                text_parts.append(f"## {text}")
            else:
                text_parts.append(text)

        full_text = "\n\n".join(text_parts)

        metadata = DocumentMetadata(
            page_count=max(page_numbers) if page_numbers else None,
            word_count=len(full_text.split()) if full_text else 0,
            char_count=len(full_text),
        )

        pages_parsed = len(page_numbers) if page_numbers else 1

        log.info(
            "unstructured_parse_complete",
            file=file_path,
            elements=len(elements),
            pages=pages_parsed,
            tables=len(tables),
        )

        return ParsedContent(
            text=full_text,
            metadata=metadata,
            tables=tables,
            pages_parsed=pages_parsed,
            pages_failed=0,
        )


def _parse_html_table(html: str) -> list[list[str]]:
    """Extract rows from an HTML table string."""
    import re

    rows: list[list[str]] = []
    row_matches = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
    for row_html in row_matches:
        cells = re.findall(r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", row_html, re.DOTALL | re.IGNORECASE)
        cells = [re.sub(r"<[^>]+>", "", cell).strip() for cell in cells]
        if cells:
            rows.append(cells)
    return rows
