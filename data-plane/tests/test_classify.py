"""Tests for POST /api/v1/classify endpoint and intelligence services."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.intelligence.classifier import Classifier
from app.services.intelligence.chunker import Chunker


@pytest.fixture
def classifier():
    return Classifier()


@pytest.fixture
def chunker():
    return Chunker()


@pytest.fixture
def client(classifier):
    app.state._test_mode = True
    app.state.classifier = classifier
    app.state.scraping = MagicMock()
    app.state.sitemap_parser = MagicMock()
    app.state.parser = MagicMock()
    with TestClient(app) as c:
        yield c


# ── Classify endpoint tests ──────────────────────────────────────────


def test_classify_funding(client):
    response = client.post("/api/v1/classify", json={
        "content": (
            "Das Förderprogramm für erneuerbare Energien gilt ab 01.04.2025. "
            "Antragsfrist bis 30.06.2025. Förderhöhe bis EUR 5.000. "
            "Kontakt: energie@wiener-neudorf.gv.at, Umweltamt."
        ),
        "language": "de",
    })
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["classification"] == "funding"
    assert data["data"]["confidence"] > 0.5
    assert "renewable_energy" in data["data"]["sub_categories"]
    assert len(data["data"]["entities"]["dates"]) >= 1
    assert len(data["data"]["entities"]["amounts"]) >= 1
    assert "energie@wiener-neudorf.gv.at" in data["data"]["entities"]["contacts"]
    assert data["data"]["summary"]
    assert data["request_id"]


def test_classify_event(client):
    response = client.post("/api/v1/classify", json={
        "content": (
            "Einladung zum Sommerfest am 15.07.2025. "
            "Die Veranstaltung findet im Kulturzentrum statt. "
            "Konzert und Ausstellung ab 18:00 Uhr."
        ),
    })
    data = response.json()
    assert data["success"] is True
    assert data["data"]["classification"] == "event"


def test_classify_policy(client):
    response = client.post("/api/v1/classify", json={
        "content": (
            "Gemeinderatsbeschluss vom 20.03.2025: "
            "Neue Verordnung zur Parkraumbewirtschaftung. "
            "Die Satzung tritt mit Kundmachung in Kraft."
        ),
    })
    data = response.json()
    assert data["success"] is True
    assert data["data"]["classification"] == "policy"


def test_classify_minutes(client):
    response = client.post("/api/v1/classify", json={
        "content": (
            "Sitzungsprotokoll der Gemeinderatssitzung vom 10.02.2025. "
            "Tagesordnung: 1. Abstimmung über Budget. "
            "2. Beschlussfassung zur Straßensanierung."
        ),
    })
    data = response.json()
    assert data["success"] is True
    assert data["data"]["classification"] == "minutes"


def test_classify_general_fallback(client):
    response = client.post("/api/v1/classify", json={
        "content": "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
    })
    data = response.json()
    assert data["success"] is True
    assert data["data"]["classification"] == "general"
    assert data["data"]["confidence"] < 0.5


def test_classify_empty_content(client):
    response = client.post("/api/v1/classify", json={
        "content": "   ",
    })
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "VALIDATION_EMPTY_CONTENT"


def test_classify_entities_extraction(client):
    response = client.post("/api/v1/classify", json={
        "content": (
            "Förderung: EUR 10.000 und € 5.000 für Projekte. "
            "Antragsfrist bis 31.12.2025. "
            "Kontakt: info@gemeinde.at und verwaltung@stadt.gv.at. "
            "Zuständig: Amt für Finanzen."
        ),
    })
    data = response.json()
    entities = data["data"]["entities"]
    assert len(entities["amounts"]) >= 2
    assert len(entities["contacts"]) >= 2
    assert len(entities["deadlines"]) >= 1


def test_classify_request_id(client):
    response = client.post(
        "/api/v1/classify",
        json={"content": "Test content for classification."},
        headers={"X-Request-ID": "classify-req-789"},
    )
    data = response.json()
    assert data["request_id"] == "classify-req-789"
    assert response.headers["X-Request-ID"] == "classify-req-789"


def test_classify_validation_missing_content(client):
    """Pydantic should reject empty content field."""
    response = client.post("/api/v1/classify", json={
        "content": "",
    })
    # min_length=1 in ClassifyRequest
    assert response.status_code == 422


# ── Classifier unit tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classifier_date_extraction(classifier):
    result = await classifier.classify(
        "Termin am 01.04.2025 und 2025-06-30 im Rathaus.",
    )
    assert "01.04.2025" in result.entities.dates
    assert "2025-06-30" in result.entities.dates


@pytest.mark.asyncio
async def test_classifier_email_extraction(classifier):
    result = await classifier.classify(
        "Kontakt: test@example.at und info@gemeinde.gv.at",
    )
    assert "test@example.at" in result.entities.contacts
    assert "info@gemeinde.gv.at" in result.entities.contacts


@pytest.mark.asyncio
async def test_classifier_amount_extraction(classifier):
    result = await classifier.classify(
        "Förderung von EUR 5.000 und € 10.000 sowie 2.500 Euro.",
    )
    assert len(result.entities.amounts) >= 2


# ── Chunker unit tests ──────────────────────────────────────────────


def test_chunker_fixed(chunker):
    text = "A" * 1000
    result = chunker.chunk(text, strategy="fixed", max_chunk_size=200, overlap=50)
    assert result.total_chunks > 1
    assert result.strategy == "fixed"
    assert all(len(c) <= 200 for c in result.chunks)


def test_chunker_sentence(chunker):
    text = "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
    result = chunker.chunk(text, strategy="sentence", max_chunk_size=50, overlap=10)
    assert result.total_chunks >= 2
    assert result.strategy == "sentence"


def test_chunker_late_chunking(chunker):
    text = "Paragraph one content here.\n\nParagraph two content here.\n\nParagraph three content."
    result = chunker.chunk(text, strategy="late_chunking", max_chunk_size=100, overlap=0)
    assert result.total_chunks >= 1
    assert result.strategy == "late_chunking"


def test_chunker_empty_text(chunker):
    result = chunker.chunk("", strategy="fixed", max_chunk_size=512)
    assert result.total_chunks == 0
    assert result.chunks == []


def test_chunker_large_paragraph(chunker):
    text = "Short paragraph.\n\n" + ("Long sentence here. " * 50) + "\n\nAnother short paragraph."
    result = chunker.chunk(text, strategy="late_chunking", max_chunk_size=200, overlap=20)
    assert result.total_chunks >= 2
    # All chunks should respect max size (with some tolerance for overlap)
    for chunk in result.chunks:
        assert len(chunk) <= 400  # Allow some overflow from overlap
