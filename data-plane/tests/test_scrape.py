"""Tests for POST /api/v1/online/scrape and POST /api/v1/online/crawl endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.scraping.scraper_service import (
    DiscoveredDocument,
    PageMetadata,
    ScrapeResult,
    ScrapeStatus,
)


@pytest.fixture
def mock_scraper():
    scraper = MagicMock()
    scraper.scrape_url = AsyncMock()
    scraper.discover_urls = AsyncMock()
    scraper.is_ready = True
    return scraper


@pytest.fixture
def mock_sitemap_parser():
    parser = MagicMock()
    parser.parse = AsyncMock(return_value=[])
    return parser


@pytest.fixture
def client(mock_scraper, mock_sitemap_parser):
    app.state._test_mode = True
    app.state.scraping = mock_scraper
    app.state.sitemap_parser = mock_sitemap_parser
    with TestClient(app) as c:
        yield c


def test_scrape_success(client, mock_scraper):
    mock_scraper.scrape_url.return_value = ScrapeResult(
        url="https://example.gv.at/page",
        status=ScrapeStatus.SUCCESS,
        markdown="# Hello\n\nSome content here.",
        metadata=PageMetadata(title="Hello", language="de", word_count=4),
        discovered_links=["https://example.gv.at/other"],
    )

    response = client.post("/api/v1/online/scrape", json={"url": "https://example.gv.at/page"})
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["url"] == "https://example.gv.at/page"
    assert data["data"]["title"] == "Hello"
    assert data["data"]["content_length"] > 0
    assert data["data"]["links_found"] == 1
    assert data["request_id"]


def test_scrape_invalid_url(client):
    response = client.post("/api/v1/online/scrape", json={"url": "not-a-url"})
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is False
    assert data["error"] == "VALIDATION_URL_INVALID"


def test_scrape_empty_content(client, mock_scraper):
    mock_scraper.scrape_url.return_value = ScrapeResult(
        url="https://example.gv.at/empty",
        status=ScrapeStatus.SUCCESS,
        markdown="   ",
        metadata=PageMetadata(),
    )

    response = client.post("/api/v1/online/scrape", json={"url": "https://example.gv.at/empty"})
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "SCRAPE_EMPTY"


def test_scrape_failed(client, mock_scraper):
    mock_scraper.scrape_url.return_value = ScrapeResult(
        url="https://example.gv.at/fail",
        status=ScrapeStatus.FAILED,
        error="Connection refused",
    )

    response = client.post("/api/v1/online/scrape", json={"url": "https://example.gv.at/fail"})
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "SCRAPE_FAILED"


def test_scrape_timeout(client, mock_scraper):
    mock_scraper.scrape_url.return_value = ScrapeResult(
        url="https://example.gv.at/slow",
        status=ScrapeStatus.TIMEOUT,
        error="Timed out",
    )

    response = client.post("/api/v1/online/scrape", json={"url": "https://example.gv.at/slow"})
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "SCRAPE_TIMEOUT"


def test_crawl_sitemap(client, mock_sitemap_parser):
    mock_sitemap_parser.parse.return_value = [
        "https://example.gv.at/page1",
        "https://example.gv.at/page2",
        "https://example.gv.at/files/doc.pdf",
    ]

    response = client.post("/api/v1/online/crawl", json={
        "url": "https://example.gv.at/sitemap.xml",
        "method": "sitemap",
    })
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["method_used"] == "sitemap"
    assert data["data"]["total_urls"] == 3

    # Check type detection
    urls = data["data"]["urls"]
    types = {u["url"]: u["type"] for u in urls}
    assert types["https://example.gv.at/page1"] == "page"
    assert types["https://example.gv.at/files/doc.pdf"] == "document"


def test_crawl_sitemap_not_found(client, mock_sitemap_parser):
    mock_sitemap_parser.parse.return_value = []

    response = client.post("/api/v1/online/crawl", json={
        "url": "https://example.gv.at/sitemap.xml",
        "method": "sitemap",
    })
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "CRAWL_SITEMAP_NOT_FOUND"


def test_crawl_bfs(client, mock_scraper):
    mock_scraper.discover_urls.return_value = (
        ["https://example.gv.at/", "https://example.gv.at/about"],
        [DiscoveredDocument(url="https://example.gv.at/doc.pdf", type="pdf")],
    )

    response = client.post("/api/v1/online/crawl", json={
        "url": "https://example.gv.at",
        "method": "crawl",
        "max_depth": 2,
        "max_urls": 50,
    })
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["method_used"] == "crawl"
    assert data["data"]["total_urls"] == 3  # 2 pages + 1 doc


def test_crawl_invalid_url(client):
    response = client.post("/api/v1/online/crawl", json={
        "url": "ftp://bad",
        "method": "sitemap",
    })
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "VALIDATION_URL_INVALID"


def test_request_id_in_scrape_response(client, mock_scraper):
    mock_scraper.scrape_url.return_value = ScrapeResult(
        url="https://example.gv.at",
        status=ScrapeStatus.SUCCESS,
        markdown="content",
        metadata=PageMetadata(word_count=1),
    )

    response = client.post(
        "/api/v1/online/scrape",
        json={"url": "https://example.gv.at"},
        headers={"X-Request-ID": "test-req-123"},
    )
    data = response.json()
    assert data["request_id"] == "test-req-123"
    assert response.headers["X-Request-ID"] == "test-req-123"
