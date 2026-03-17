"""File type detection — uses content-type headers, magic bytes, and file extension."""

from pathlib import Path

from app.services.parsing.models import DocumentType

MIME_MAP: dict[str, DocumentType] = {
    "application/pdf": DocumentType.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DocumentType.DOCX,
    "application/msword": DocumentType.DOC,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": DocumentType.XLSX,
    "application/vnd.ms-excel": DocumentType.XLS,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": DocumentType.PPTX,
    "text/plain": DocumentType.TXT,
    "text/csv": DocumentType.CSV,
    "text/html": DocumentType.HTML,
    "application/rtf": DocumentType.RTF,
    "application/vnd.oasis.opendocument.text": DocumentType.ODT,
}

MAGIC_BYTES: list[tuple[bytes, DocumentType]] = [
    (b"%PDF", DocumentType.PDF),
    (b"PK\x03\x04", DocumentType.DOCX),  # ZIP-based (DOCX, XLSX, PPTX, ODT)
    (b"\xd0\xcf\x11\xe0", DocumentType.DOC),  # OLE2 (DOC, XLS, PPT)
    (b"<!DOCTYPE", DocumentType.HTML),
    (b"<html", DocumentType.HTML),
]

EXTENSION_MAP: dict[str, DocumentType] = {
    ".pdf": DocumentType.PDF,
    ".docx": DocumentType.DOCX,
    ".doc": DocumentType.DOC,
    ".xlsx": DocumentType.XLSX,
    ".xls": DocumentType.XLS,
    ".pptx": DocumentType.PPTX,
    ".txt": DocumentType.TXT,
    ".csv": DocumentType.CSV,
    ".html": DocumentType.HTML,
    ".htm": DocumentType.HTML,
    ".rtf": DocumentType.RTF,
    ".odt": DocumentType.ODT,
}


def detect_from_mime(content_type: str | None) -> DocumentType | None:
    if not content_type:
        return None
    mime = content_type.split(";")[0].strip().lower()
    return MIME_MAP.get(mime)


def detect_from_extension(filename: str | None = None, url: str | None = None) -> DocumentType | None:
    target = filename or url
    if not target:
        return None
    clean = target.split("?")[0].split("#")[0]
    ext = Path(clean).suffix.lower()
    return EXTENSION_MAP.get(ext)


def detect_from_bytes(data: bytes) -> DocumentType | None:
    if len(data) < 8:
        return None
    for magic, doc_type in MAGIC_BYTES:
        if data[: len(magic)] == magic:
            if doc_type == DocumentType.DOCX:
                return _detect_zip_subtype(data)
            return doc_type
    return None


def _detect_zip_subtype(data: bytes) -> DocumentType:
    if b"word/" in data[:4096]:
        return DocumentType.DOCX
    if b"xl/" in data[:4096]:
        return DocumentType.XLSX
    if b"ppt/" in data[:4096]:
        return DocumentType.PPTX
    if b"content.xml" in data[:4096]:
        return DocumentType.ODT
    return DocumentType.DOCX


def detect_document_type(
    content_type: str | None = None,
    filename: str | None = None,
    url: str | None = None,
    data: bytes | None = None,
) -> DocumentType:
    """Detect document type using all available signals. Priority: MIME > magic bytes > extension."""
    if result := detect_from_mime(content_type):
        return result
    if data and (result := detect_from_bytes(data)):
        return result
    if result := detect_from_extension(filename, url):
        return result
    return DocumentType.UNKNOWN
