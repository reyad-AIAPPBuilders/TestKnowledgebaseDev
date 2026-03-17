"""Base parser interface."""

from abc import ABC, abstractmethod

from app.services.parsing.models import DocumentMetadata, ParseOptions, TableData


class ParsedContent:
    """Result from a parser implementation."""

    def __init__(
        self,
        text: str,
        metadata: DocumentMetadata | None = None,
        tables: list[TableData] | None = None,
        pages_parsed: int | None = None,
        pages_failed: int | None = None,
    ):
        self.text = text
        self.metadata = metadata or DocumentMetadata()
        self.tables = tables or []
        self.pages_parsed = pages_parsed
        self.pages_failed = pages_failed


class BaseParser(ABC):
    """Interface that all document parsers must implement."""

    @abstractmethod
    async def parse(self, file_path: str, options: ParseOptions) -> ParsedContent:
        """Parse a document file and return extracted content."""
        ...

    @abstractmethod
    def supports(self) -> list[str]:
        """Return list of supported document types (e.g., ['pdf', 'docx'])."""
        ...
