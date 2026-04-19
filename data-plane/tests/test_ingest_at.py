"""Tests for ``POST /api/v1/online/ingest/at`` and its pure helpers.

Covers the AT funding-assistant ingest: pure helpers for province
normalization and ID derivation, plus end-to-end endpoint cases driven
through the real router + real Chunker with mocked embedder / extractor /
Qdrant so we can inspect the exact upsert payload.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.online.ingest_at import (
    ALL_AT_COLLECTIONS,
    PROVINCE_TO_COLLECTION_AT,
    _normalize_provinces,
    _point_id,
    _resolve_collection,
    _select_collections,
)
from app.services.embedding.bge_m3_client import EmbeddingResult
from app.services.intelligence.chunker import Chunker


# ─────────────────────────────────────────────────────────────────────
# Pure helpers — no FastAPI harness needed
# ─────────────────────────────────────────────────────────────────────


class TestResolveCollection:
    def test_english_lowercase_maps_to_german(self):
        assert _resolve_collection("lower austria") == "Niederösterreich"
        assert _resolve_collection("vienna") == "Wien"
        assert _resolve_collection("carinthia") == "Kärnten"

    def test_german_form_passes_through(self):
        assert _resolve_collection("Niederösterreich") == "Niederösterreich"
        assert _resolve_collection("Wien") == "Wien"

    def test_case_insensitive_german(self):
        assert _resolve_collection("wien") == "Wien"
        assert _resolve_collection("TIROL") == "Tirol"

    def test_whitespace_is_trimmed(self):
        assert _resolve_collection("  vienna  ") == "Wien"

    def test_unknown_returns_none(self):
        assert _resolve_collection("transnistria") is None
        assert _resolve_collection("") is None
        assert _resolve_collection("bavaria") is None  # DE province, not AT

    def test_all_nine_provinces_resolve(self):
        resolved = {_resolve_collection(k) for k in PROVINCE_TO_COLLECTION_AT}
        assert resolved == set(ALL_AT_COLLECTIONS)
        assert len(ALL_AT_COLLECTIONS) == 9


class TestNormalizeProvinces:
    def test_mixed_case_and_language_dedupes(self):
        result = _normalize_provinces(["lower austria", "Niederösterreich", "LOWER AUSTRIA"])
        assert result == ["Niederösterreich"]

    def test_drops_unknown_values(self):
        result = _normalize_provinces(["lower austria", "transnistria", "vienna"])
        assert result == sorted(["Niederösterreich", "Wien"])

    def test_empty_inputs(self):
        assert _normalize_provinces([]) == []
        assert _normalize_provinces(None) == []

    def test_sorts_output(self):
        result = _normalize_provinces(["vienna", "burgenland", "tyrol"])
        assert result == sorted(["Wien", "Burgenland", "Tirol"])


class TestSelectCollections:
    def test_override_wins_over_extractor(self):
        assert _select_collections(["Tirol"], ["Wien"]) == ["Tirol"]

    def test_extractor_used_when_no_override(self):
        assert _select_collections([], ["Salzburg", "Tirol"]) == ["Salzburg", "Tirol"]

    def test_all_nine_when_both_empty(self):
        assert _select_collections([], []) == ALL_AT_COLLECTIONS


class TestComposeBaseUrl:
    """Ensures QDRANT_URL_AT + QDRANT_PORT_AT compose correctly, matching the
    upstream qdrant-client pattern (URL and port supplied separately)."""

    def test_appends_port_to_hostname_url(self):
        from app.services.embedding.qdrant_service import _compose_base_url
        assert _compose_base_url("https://at-qdrant.example.com", 443) == "https://at-qdrant.example.com:443"

    def test_http_localhost_with_port(self):
        from app.services.embedding.qdrant_service import _compose_base_url
        assert _compose_base_url("http://localhost", 6333) == "http://localhost:6333"

    def test_explicit_port_in_url_wins(self):
        from app.services.embedding.qdrant_service import _compose_base_url
        # URL already carries :6333 → ignore the kwarg to avoid double-port.
        assert _compose_base_url("http://qdrant:6333", 443) == "http://qdrant:6333"

    def test_none_port_leaves_url_unchanged(self):
        from app.services.embedding.qdrant_service import _compose_base_url
        assert _compose_base_url("http://qdrant:6333", None) == "http://qdrant:6333"
        assert _compose_base_url("https://at-qdrant.example.com", None) == "https://at-qdrant.example.com"

    def test_zero_port_treated_as_no_port(self):
        from app.services.embedding.qdrant_service import _compose_base_url
        assert _compose_base_url("http://qdrant:6333", 0) == "http://qdrant:6333"

    def test_strips_trailing_slash(self):
        from app.services.embedding.qdrant_service import _compose_base_url
        assert _compose_base_url("https://at-qdrant.example.com/", 443) == "https://at-qdrant.example.com:443"


class TestPointId:
    def test_deterministic(self):
        assert _point_id("src1", 0, "Wien") == _point_id("src1", 0, "Wien")

    def test_changes_with_chunk_index(self):
        a = _point_id("src1", 0, "Wien")
        b = _point_id("src1", 1, "Wien")
        assert a != b

    def test_changes_with_collection(self):
        a = _point_id("src1", 0, "Wien")
        b = _point_id("src1", 0, "Tirol")
        assert a != b

    def test_fits_uint64(self):
        pid = _point_id("src1", 0, "Wien")
        assert 0 <= pid < (1 << 64)


# ─────────────────────────────────────────────────────────────────────
# Endpoint end-to-end — real router, mocked services
# ─────────────────────────────────────────────────────────────────────


def _dummy_embed(dim: int = 1536):
    async def _embed_batch(chunks):
        return [EmbeddingResult(dense=[0.01 * (i + 1)] * dim) for i, _ in enumerate(chunks)]
    return _embed_batch


def _install(*, extract_return: dict | None = None, embed_dim: int = 1536):
    """Wire minimal mocks onto app.state for the AT endpoint.

    Returns a dict with handles for post-assertion inspection.
    """
    app.state._test_mode = True

    chunker = Chunker()
    app.state.chunker = chunker

    # Contextual enricher: passthrough so output chunks == input chunks.
    enricher = MagicMock()
    enricher.enrich_chunks = AsyncMock(side_effect=lambda document, chunks: list(chunks))
    app.state.contextual_enricher = enricher

    embedder = MagicMock()
    embedder.embed_batch = AsyncMock(side_effect=_dummy_embed(embed_dim))
    app.state.openai_embedder = embedder

    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=extract_return or {})
    app.state.funding_extractor = extractor

    qdrant = MagicMock()
    qdrant.delete_by_filter = AsyncMock(return_value=0)
    qdrant.upsert_points = AsyncMock(side_effect=lambda _col, points: len(points))
    app.state.qdrant_at = qdrant

    return {"chunker": chunker, "embedder": embedder, "extractor": extractor, "qdrant": qdrant}


BASE_PAYLOAD = {
    "source_id": "web_foerderungen_001",
    "url": "https://www.salzburg.gv.at/foerderungen",
    "content": "Sportförderung des Landes Salzburg. Die Förderung unterstützt Vereine.",
    "content_type": ["funding", "sport"],
    "language": "de",
    "metadata": {
        "assistant_id": "asst_foerder_at_01",
        "municipality_id": "land-salzburg",
        "title": "Sportförderung Salzburg",
        "source_type": "web",
    },
}


def _collections_upserted(qdrant_mock) -> list[str]:
    return [call.args[0] for call in qdrant_mock.upsert_points.await_args_list]


def _all_points(qdrant_mock) -> list[dict]:
    out = []
    for call in qdrant_mock.upsert_points.await_args_list:
        out.extend(call.args[1])
    return out


class TestEndpoint:
    def test_override_in_english_normalizes_to_german(self):
        handles = _install(extract_return={"state_or_province": ["salzburg"]})
        payload = {**BASE_PAYLOAD, "state_or_province": ["lower austria", "vienna"]}

        with TestClient(app) as c:
            r = c.post("/api/v1/online/ingest/at", json=payload)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        # Override wins over extractor's ["salzburg"]; English→German; sorted.
        assert sorted(body["data"]["collections_written"]) == ["Niederösterreich", "Wien"]
        assert sorted(_collections_upserted(handles["qdrant"])) == ["Niederösterreich", "Wien"]

        # Every point carries normalized German state_or_province.
        for p in _all_points(handles["qdrant"]):
            assert p["payload"]["metadata"]["state_or_province"] == ["Niederösterreich", "Wien"]

    def test_override_in_german_passes_through(self):
        handles = _install()
        payload = {**BASE_PAYLOAD, "state_or_province": ["Niederösterreich"]}

        with TestClient(app) as c:
            r = c.post("/api/v1/online/ingest/at", json=payload)

        assert r.status_code == 200, r.text
        assert r.json()["data"]["collections_written"] == ["Niederösterreich"]

    def test_extractor_drives_selection_when_no_override(self):
        handles = _install(extract_return={"state_or_province": ["tyrol", "salzburg"]})
        payload = {**BASE_PAYLOAD}  # no state_or_province override

        with TestClient(app) as c:
            r = c.post("/api/v1/online/ingest/at", json=payload)

        assert r.status_code == 200, r.text
        assert sorted(r.json()["data"]["collections_written"]) == ["Salzburg", "Tirol"]

        # Region per point in a specific-province fan-out is the single collection name.
        for p in _all_points(handles["qdrant"]):
            region = p["payload"]["metadata"]["region"]
            assert isinstance(region, list) and len(region) == 1
            assert region[0] in {"Salzburg", "Tirol"}

    def test_nationwide_fanout_stores_alle_sentinel(self):
        """Extractor returns empty list and no override → all 9 collections,
        every point's metadata.region == ['alle']."""
        handles = _install(extract_return={"state_or_province": []})

        with TestClient(app) as c:
            r = c.post("/api/v1/online/ingest/at", json=BASE_PAYLOAD)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert set(body["data"]["collections_written"]) == set(ALL_AT_COLLECTIONS)

        points = _all_points(handles["qdrant"])
        assert points, "Expected points upserted across all 9 collections"
        for p in points:
            assert p["payload"]["metadata"]["region"] == ["alle"]

    def test_bogus_override_falls_back_to_extractor(self):
        """Override entries that don't resolve to any AT province are dropped;
        the extractor's list then drives selection."""
        handles = _install(extract_return={"state_or_province": ["vienna"]})
        payload = {**BASE_PAYLOAD, "state_or_province": ["transnistria", "atlantis"]}

        with TestClient(app) as c:
            r = c.post("/api/v1/online/ingest/at", json=payload)

        assert r.status_code == 200, r.text
        assert r.json()["data"]["collections_written"] == ["Wien"]

    def test_point_shape_matches_existing_schema(self):
        """Points must carry an integer ID and a flat 1536-float vector (not a
        named dict), matching the existing AT collection schema."""
        handles = _install()
        payload = {**BASE_PAYLOAD, "state_or_province": ["Tirol"]}

        with TestClient(app) as c:
            r = c.post("/api/v1/online/ingest/at", json=payload)

        assert r.status_code == 200, r.text
        points = _all_points(handles["qdrant"])
        assert len(points) > 0
        for p in points:
            assert isinstance(p["id"], int)
            assert 0 <= p["id"] < (1 << 64)
            assert isinstance(p["vector"], list)
            assert len(p["vector"]) == 1536
            assert all(isinstance(v, float) for v in p["vector"])

    def test_metadata_includes_source_id_and_omits_chunk_fields(self):
        """source_id is written to every point's metadata; chunk_id and
        chunk_index are intentionally not — they're only used to derive
        the deterministic integer point ID."""
        handles = _install()
        payload = {**BASE_PAYLOAD, "state_or_province": ["Wien"]}

        with TestClient(app) as c:
            r = c.post("/api/v1/online/ingest/at", json=payload)

        assert r.status_code == 200
        points = _all_points(handles["qdrant"])
        assert len(points) > 0
        for p in points:
            md = p["payload"]["metadata"]
            assert md["source_id"] == payload["source_id"]
            assert "chunk_id" not in md
            assert "chunk_index" not in md
            assert md["source_url"] == payload["url"]
            assert md["region"] == ["Wien"]

    def test_deletes_by_source_url_before_upsert(self):
        """Idempotency path: we delete by the indexed metadata.source_url
        filter before upserting (since metadata.source_id isn't indexed)."""
        handles = _install()
        payload = {**BASE_PAYLOAD, "state_or_province": ["Wien"]}

        with TestClient(app) as c:
            r = c.post("/api/v1/online/ingest/at", json=payload)

        assert r.status_code == 200
        qdrant = handles["qdrant"]
        qdrant.delete_by_filter.assert_awaited()
        # Any delete call uses the source_url key.
        for call in qdrant.delete_by_filter.await_args_list:
            flt = call.args[1]
            assert flt["must"][0]["key"] == "metadata.source_url"
            assert flt["must"][0]["match"]["value"] == payload["url"]

    def test_whitespace_only_content_returns_validation_error_envelope(self):
        _install()
        payload = {**BASE_PAYLOAD, "content": "   "}

        with TestClient(app) as c:
            r = c.post("/api/v1/online/ingest/at", json=payload)

        # Pydantic's min_length=1 only checks length, so the handler catches
        # whitespace-only content and returns the error envelope.
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is False
        assert body["error"] == "VALIDATION_EMPTY_CONTENT"

    def test_truly_empty_content_rejected_by_pydantic(self):
        _install()
        payload = {**BASE_PAYLOAD, "content": ""}

        with TestClient(app) as c:
            r = c.post("/api/v1/online/ingest/at", json=payload)

        # Empty string hits pydantic's min_length=1 and 422s before the handler.
        assert r.status_code == 422

    def test_funding_extractor_called_with_at_country(self):
        handles = _install()

        with TestClient(app) as c:
            c.post("/api/v1/online/ingest/at", json=BASE_PAYLOAD)

        kwargs = handles["extractor"].extract.await_args.kwargs
        assert kwargs["country"] == "AT"

    def test_response_metadata_counts(self):
        handles = _install()
        payload = {**BASE_PAYLOAD, "state_or_province": ["Tirol", "Wien"]}

        with TestClient(app) as c:
            r = c.post("/api/v1/online/ingest/at", json=payload)

        body = r.json()["data"]
        # Two collections, N chunks each → vectors_stored is 2*N.
        assert body["vectors_stored"] == 2 * body["chunks_created"]
        assert body["content_type"] == ["funding", "sport"]
        assert body["source_id"] == payload["source_id"]


# ─────────────────────────────────────────────────────────────────────
# Error-path tests
# ─────────────────────────────────────────────────────────────────────


def test_embedder_failure_returns_error_envelope():
    from app.services.embedding.bge_m3_client import EmbeddingError

    async def _fail(_chunks):
        raise EmbeddingError("OpenAI outage")

    handles = _install()
    handles["embedder"].embed_batch = AsyncMock(side_effect=_fail)

    with TestClient(app) as c:
        r = c.post("/api/v1/online/ingest/at", json=BASE_PAYLOAD)

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["error"] in {"EMBEDDING_FAILED", "EMBEDDING_OOM", "EMBEDDING_MODEL_NOT_LOADED"}


def test_upsert_disk_full_mapped_to_error_code():
    from app.services.embedding.qdrant_service import QdrantError

    async def _upsert_disk_full(_col, _points):
        raise QdrantError("disk is full")

    handles = _install()
    handles["qdrant"].upsert_points = AsyncMock(side_effect=_upsert_disk_full)

    payload = {**BASE_PAYLOAD, "state_or_province": ["Wien"]}
    with TestClient(app) as c:
        r = c.post("/api/v1/online/ingest/at", json=payload)

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "QDRANT_DISK_FULL"
