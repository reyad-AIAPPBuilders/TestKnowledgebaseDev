"""Internal models for the parsing subsystem."""

from enum import Enum

from pydantic import BaseModel, Field


class ParseStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


class DocumentType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"
    XLSX = "xlsx"
    XLS = "xls"
    PPTX = "pptx"
    TXT = "txt"
    CSV = "csv"
    HTML = "html"
    RTF = "rtf"
    ODT = "odt"
    UNKNOWN = "unknown"


class ParseOptions(BaseModel):
    extract_tables: bool = True
    extract_images: bool = False
    ocr_enabled: bool = True
    ocr_language: str = "deu+eng"
    max_pages: int | None = None
    page_separator: str = "\n\n---\n\n"


class DocumentMetadata(BaseModel):
    title: str | None = None
    author: str | None = None
    subject: str | None = None
    creator: str | None = None
    creation_date: str | None = None
    modification_date: str | None = None
    page_count: int | None = None
    word_count: int = 0
    char_count: int = 0
    language: str | None = None
    file_size_bytes: int = 0
    mime_type: str | None = None


class TableData(BaseModel):
    page: int | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class ParseResult(BaseModel):
    status: ParseStatus
    document_type: DocumentType
    text: str | None = None
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    tables: list[TableData] = Field(default_factory=list)
    source_url: str | None = None
    filename: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    pages_parsed: int | None = None
    pages_failed: int | None = None
