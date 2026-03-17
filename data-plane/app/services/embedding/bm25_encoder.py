"""BM25 sparse vector encoder for hybrid search.

Tokenizes text and produces sparse vectors with term frequencies.
Designed to work with Qdrant's IDF modifier on the collection config,
so Qdrant handles the IDF weighting — we only need to provide TF values.
"""

import hashlib
import re

from app.utils.logger import get_logger

log = get_logger(__name__)

# Simple stopwords for German and English
STOPWORDS = frozenset({
    # German
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "eines",
    "und", "oder", "aber", "wenn", "als", "auch", "auf", "aus", "bei", "bis",
    "für", "mit", "nach", "von", "zu", "zum", "zur", "im", "in", "an", "am",
    "um", "ist", "sind", "war", "hat", "haben", "wird", "werden", "kann",
    "nicht", "sich", "es", "er", "sie", "wir", "ich", "dass", "wie", "so",
    "noch", "nur", "über", "durch", "vor", "schon", "sehr", "mehr", "hier",
    # English
    "the", "a", "an", "and", "or", "but", "if", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "is", "are", "was", "were", "be",
    "been", "has", "have", "had", "do", "does", "did", "will", "would",
    "can", "could", "not", "this", "that", "it", "he", "she", "we", "they",
})

# Max index for sparse vector (stay within uint32 range for Qdrant)
MAX_SPARSE_INDEX = 2**31 - 1


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, filter stopwords and short tokens."""
    tokens = re.findall(r"[a-zäöüß0-9]+", text.lower())
    return [t for t in tokens if len(t) > 1 and t not in STOPWORDS]


def _token_to_index(token: str) -> int:
    """Stable hash of a token to a sparse vector index."""
    h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
    return h % MAX_SPARSE_INDEX


class BM25Encoder:
    """Produces sparse vectors from text for BM25-style hybrid search."""

    def encode(self, text: str) -> dict:
        """Encode text into a Qdrant-compatible sparse vector.

        Returns {"indices": [...], "values": [...]} with term frequencies.
        Qdrant's IDF modifier handles inverse document frequency weighting.
        """
        tokens = _tokenize(text)
        if not tokens:
            return {"indices": [], "values": []}

        # Count term frequencies
        tf: dict[int, float] = {}
        for token in tokens:
            idx = _token_to_index(token)
            tf[idx] = tf.get(idx, 0.0) + 1.0

        # Sort by index for Qdrant
        sorted_indices = sorted(tf.keys())
        return {
            "indices": sorted_indices,
            "values": [tf[idx] for idx in sorted_indices],
        }

    def encode_batch(self, texts: list[str]) -> list[dict]:
        """Encode multiple texts into sparse vectors."""
        return [self.encode(text) for text in texts]
