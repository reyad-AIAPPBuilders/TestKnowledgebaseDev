"""Text chunking strategies for the ingest pipeline.

Supports multiple chunking strategies:
- fixed: Split by character count with overlap
- sentence: Split on sentence boundaries
- late_chunking: Semantic paragraph-aware splitting (default for BGE-M3)
"""

import re

from app.services.intelligence.models import ChunkResult
from app.utils.logger import get_logger

log = get_logger(__name__)


class Chunker:
    """Split text into chunks for embedding."""

    def chunk(
        self,
        text: str,
        strategy: str = "late_chunking",
        max_chunk_size: int = 512,
        overlap: int = 50,
    ) -> ChunkResult:
        if not text or not text.strip():
            return ChunkResult(chunks=[], total_chunks=0, strategy=strategy, avg_chunk_size=0)

        if strategy == "fixed":
            chunks = self._fixed_chunks(text, max_chunk_size, overlap)
        elif strategy == "sentence":
            chunks = self._sentence_chunks(text, max_chunk_size, overlap)
        else:
            chunks = self._late_chunking(text, max_chunk_size, overlap)

        # Filter out empty chunks
        chunks = [c.strip() for c in chunks if c.strip()]

        avg_size = sum(len(c) for c in chunks) // max(len(chunks), 1)

        log.info(
            "chunking_complete",
            strategy=strategy,
            total_chunks=len(chunks),
            avg_chunk_size=avg_size,
        )

        return ChunkResult(
            chunks=chunks,
            total_chunks=len(chunks),
            strategy=strategy,
            avg_chunk_size=avg_size,
        )

    def _fixed_chunks(self, text: str, max_size: int, overlap: int) -> list[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = start + max_size
            chunk = text[start:end]
            chunks.append(chunk)
            start = end - overlap
            if start >= len(text):
                break
        return chunks

    def _sentence_chunks(self, text: str, max_size: int, overlap: int) -> list[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks = []
        current = ""

        for sentence in sentences:
            if len(current) + len(sentence) + 1 > max_size and current:
                chunks.append(current)
                # Overlap: keep last portion
                if overlap > 0 and len(current) > overlap:
                    current = current[-overlap:] + " " + sentence
                else:
                    current = sentence
            else:
                current = current + " " + sentence if current else sentence

        if current.strip():
            chunks.append(current)

        return chunks

    def _late_chunking(self, text: str, max_size: int, overlap: int) -> list[str]:
        """Paragraph-aware chunking that respects document structure.

        Splits on double newlines (paragraphs) first, then merges small
        paragraphs and splits large ones to stay within max_size.
        """
        paragraphs = re.split(r"\n\s*\n", text)
        chunks = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # If a single paragraph exceeds max_size, split it by sentences
            if len(para) > max_size:
                if current.strip():
                    chunks.append(current)
                    current = ""
                sub_chunks = self._sentence_chunks(para, max_size, overlap)
                chunks.extend(sub_chunks)
                continue

            if len(current) + len(para) + 2 > max_size and current:
                chunks.append(current)
                # Overlap: keep tail of previous chunk
                if overlap > 0 and len(current) > overlap:
                    current = current[-overlap:] + "\n\n" + para
                else:
                    current = para
            else:
                current = current + "\n\n" + para if current else para

        if current.strip():
            chunks.append(current)

        return chunks
