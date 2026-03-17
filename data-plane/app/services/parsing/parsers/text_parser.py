"""Plain text, CSV, HTML, and RTF parser."""

import csv
import io

import charset_normalizer

from app.services.parsing.models import DocumentMetadata, DocumentType, ParseOptions, TableData
from app.services.parsing.parsers.base import BaseParser, ParsedContent
from app.utils.logger import get_logger

log = get_logger(__name__)


class TextParser(BaseParser):
    """Parse plain text, CSV, HTML, and RTF files."""

    def supports(self) -> list[str]:
        return [
            DocumentType.TXT.value,
            DocumentType.CSV.value,
            DocumentType.HTML.value,
            DocumentType.RTF.value,
        ]

    async def parse(self, file_path: str, options: ParseOptions) -> ParsedContent:
        with open(file_path, "rb") as f:
            raw = f.read()

        result = charset_normalizer.from_bytes(raw)
        best = result.best()
        encoding = best.encoding if best else "utf-8"

        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            text = raw.decode("utf-8", errors="replace")

        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else "txt"

        tables: list[TableData] = []

        if ext == "csv":
            text, tables = self._parse_csv(text, options)
        elif ext in ("html", "htm"):
            text = self._strip_html(text)
        elif ext == "rtf":
            text = self._strip_rtf(text)

        metadata = DocumentMetadata(
            word_count=len(text.split()) if text else 0,
            char_count=len(text),
            file_size_bytes=len(raw),
        )

        return ParsedContent(
            text=text,
            metadata=metadata,
            tables=tables,
            pages_parsed=1,
            pages_failed=0,
        )

    def _parse_csv(self, text: str, options: ParseOptions) -> tuple[str, list[TableData]]:
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)

        if not rows:
            return "", []

        text_lines = []
        for row in rows:
            text_lines.append(" | ".join(row))

        tables: list[TableData] = []
        if options.extract_tables and len(rows) >= 1:
            tables.append(TableData(
                headers=rows[0],
                rows=rows[1:] if len(rows) > 1 else [],
            ))

        return "\n".join(text_lines), tables

    def _strip_html(self, html: str) -> str:
        import re

        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</(?:p|div|h[1-6]|li|tr|td|th)>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)

        import html as html_mod
        text = html_mod.unescape(text)

        lines = [line.strip() for line in text.split("\n")]
        return "\n".join(line for line in lines if line)

    def _strip_rtf(self, rtf: str) -> str:
        import re

        text = re.sub(r"\\[a-z]+\d*\s?", " ", rtf)
        text = re.sub(r"[{}]", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()
