"""
POST /api/v1/online/scrape — Scrape a single webpage (Crawl4AI)
POST /api/v1/online/crawl  — Discover URLs from site/sitemap
"""

import asyncio

from fastapi import APIRouter, Request

from app.models.common import ErrorCode, ResponseEnvelope
from app.models.online.scrape import (
    CrawlData,
    CrawlRequest,
    CrawlUrl,
    InnerDocData,
    InnerImageData,
    ScrapeData,
    ScrapeRequest,
)
from app.services.parsing.models import ParseStatus
from app.services.scraping.document_discovery import discover_images, document_type
from app.services.scraping.scraper_service import ScrapeOptions, ScrapeStatus
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/online", tags=["Online - Web Scraping"])


def _validate_url(url: str) -> str | None:
    """Return error message if invalid, None if valid."""
    url = url.strip()
    if not url:
        return "URL is required"
    if not url.startswith(("http://", "https://")):
        return "URL must start with http:// or https://"
    return None


@router.post(
    "/scrape",
    summary="Scrape a single webpage",
    description="Scrape a webpage using Crawl4AI (with JavaScript rendering) and return the extracted content as Markdown. Includes title, language detection, and link discovery. Results are cached in Redis.\n\n**Optional X-API-Key header** — required only when `DP_ONLINE_API_KEYS` is configured.\n\n**Error codes:** `VALIDATION_URL_INVALID`, `SCRAPE_FAILED`, `SCRAPE_BLOCKED`, `SCRAPE_TIMEOUT`, `SCRAPE_EMPTY`, `SCRAPE_ROBOTS_BLOCKED`",
    response_description="Scraped page content as Markdown with metadata",
)
async def scrape(body: ScrapeRequest, request: Request) -> ResponseEnvelope[ScrapeData]:
    request_id = request.state.request_id
    scraper = request.app.state.scraping

    validation_error = _validate_url(body.url)
    if validation_error:
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.VALIDATION_URL_INVALID,
            detail=validation_error,
            request_id=request_id,
        )

    options = ScrapeOptions(js_render=True, extract_links=True, timeout=30)
    result = await scraper.scrape_url(body.url, options, request_id=request_id)

    if result.status != ScrapeStatus.SUCCESS:
        error_code = _map_scrape_error(result.status, result.error)
        return ResponseEnvelope(
            success=False,
            error=error_code,
            detail=result.error,
            request_id=request_id,
        )

    content = result.markdown or ""
    if not content.strip():
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.SCRAPE_EMPTY,
            detail="Page returned no extractable content",
            request_id=request_id,
        )

    # ── Parse inner images if requested ──
    inner_images: list[InnerImageData] | None = None
    if body.inner_img and result.html:
        discovered = discover_images(result.html, body.url)
        if discovered:
            parser = request.app.state.parser
            inner_images = await _parse_inner_images(parser, discovered, request_id)

    # ── Parse inner documents if requested ──
    inner_documents: list[InnerDocData] | None = None
    if body.inner_docs and result.discovered_documents:
        parser = request.app.state.parser
        inner_documents = await _parse_inner_documents(
            parser, result.discovered_documents, request_id
        )

    return ResponseEnvelope(
        success=True,
        data=ScrapeData(
            url=result.url,
            title=result.metadata.title,
            content=content,
            content_length=len(content),
            language=result.metadata.language,
            links_found=len(result.discovered_links),
            last_modified=None,
            inner_images=inner_images,
            inner_documents=inner_documents,
        ),
        request_id=request_id,
    )


@router.post(
    "/crawl",
    summary="Discover URLs from a website",
    description="Discover all URLs on a website using either sitemap parsing or BFS link crawling.\n\n- **sitemap**: Parse XML sitemaps (including nested sitemaps and robots.txt sitemap references)\n- **crawl**: Follow links via breadth-first search up to `max_depth` levels\n\nEach URL is classified as either `page` (HTML) or `document` (PDF, DOCX, etc.).\n\n**Optional X-API-Key header** — required only when `DP_ONLINE_API_KEYS` is configured.\n\n**Error codes:** `VALIDATION_URL_INVALID`, `CRAWL_SITEMAP_NOT_FOUND`",
    response_description="List of discovered URLs with type classification",
)
async def crawl(body: CrawlRequest, request: Request) -> ResponseEnvelope[CrawlData]:
    request_id = request.state.request_id
    scraper = request.app.state.scraping
    sitemap_parser = request.app.state.sitemap_parser

    validation_error = _validate_url(body.url)
    if validation_error:
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.VALIDATION_URL_INVALID,
            detail=validation_error,
            request_id=request_id,
        )

    if body.method == "sitemap":
        urls = await sitemap_parser.parse(body.url, max_urls=body.max_urls)
        if not urls:
            return ResponseEnvelope(
                success=False,
                error=ErrorCode.CRAWL_SITEMAP_NOT_FOUND,
                detail="No URLs found in sitemap",
                request_id=request_id,
            )

        crawl_urls = []
        for u in urls:
            doc_type = document_type(u)
            crawl_urls.append(CrawlUrl(
                url=u,
                type="document" if doc_type else "page",
                last_modified=None,
            ))

        return ResponseEnvelope(
            success=True,
            data=CrawlData(
                base_url=body.url,
                method_used="sitemap",
                urls=crawl_urls,
                total_urls=len(crawl_urls),
            ),
            request_id=request_id,
        )

    # method == "crawl" — BFS discovery
    pages, docs = await scraper.discover_urls(
        body.url,
        max_depth=body.max_depth,
        max_pages=body.max_urls,
        same_domain_only=True,
    )

    crawl_urls = [CrawlUrl(url=u, type="page", last_modified=None) for u in pages]
    crawl_urls += [CrawlUrl(url=d.url, type="document", last_modified=None) for d in docs]

    return ResponseEnvelope(
        success=True,
        data=CrawlData(
            base_url=body.url,
            method_used="crawl",
            urls=crawl_urls,
            total_urls=len(crawl_urls),
        ),
        request_id=request_id,
    )


async def _parse_inner_images(
    parser, images: list, request_id: str
) -> list[InnerImageData]:
    """Parse each discovered image URL via the ParserService (LlamaParse OCR) concurrently."""

    async def _parse_one(img) -> InnerImageData:
        try:
            parse_result = await parser.parse_from_url(img.url)
            if parse_result.status == ParseStatus.SUCCESS and parse_result.text:
                return InnerImageData(
                    url=img.url,
                    alt=img.alt,
                    title=img.title,
                    content=parse_result.text,
                    content_length=len(parse_result.text),
                )
            else:
                return InnerImageData(
                    url=img.url,
                    alt=img.alt,
                    title=img.title,
                    error=parse_result.error or f"Parse failed: {parse_result.status.value}",
                )
        except Exception as exc:
            log.warning("inner_img_parse_failed", url=img.url, error=str(exc))
            return InnerImageData(
                url=img.url,
                alt=img.alt,
                title=img.title,
                error=str(exc),
            )

    results = await asyncio.gather(*[_parse_one(img) for img in images])
    return list(results)


async def _parse_inner_documents(
    parser, documents: list, request_id: str
) -> list[InnerDocData]:
    """Parse each discovered document URL via the ParserService concurrently."""

    async def _parse_one(doc) -> InnerDocData:
        try:
            parse_result = await parser.parse_from_url(doc.url)
            if parse_result.status == ParseStatus.SUCCESS:
                return InnerDocData(
                    url=doc.url,
                    title=doc.link_text or parse_result.metadata.title,
                    doc_type=doc.type,
                    content=parse_result.text,
                    pages=parse_result.pages_parsed,
                    content_length=len(parse_result.text) if parse_result.text else 0,
                    language=parse_result.metadata.language,
                )
            else:
                return InnerDocData(
                    url=doc.url,
                    title=doc.link_text,
                    doc_type=doc.type,
                    error=parse_result.error or f"Parse failed: {parse_result.status.value}",
                )
        except Exception as exc:
            log.warning("inner_doc_parse_failed", url=doc.url, error=str(exc))
            return InnerDocData(
                url=doc.url,
                title=doc.link_text,
                doc_type=doc.type,
                error=str(exc),
            )

    results = await asyncio.gather(*[_parse_one(doc) for doc in documents])
    return list(results)


def _map_scrape_error(status: str, error_msg: str | None) -> str:
    if status == ScrapeStatus.TIMEOUT:
        return ErrorCode.SCRAPE_TIMEOUT
    if status == ScrapeStatus.BLOCKED:
        error_lower = (error_msg or "").lower()
        if "robot" in error_lower:
            return ErrorCode.SCRAPE_ROBOTS_BLOCKED
        return ErrorCode.SCRAPE_BLOCKED
    return ErrorCode.SCRAPE_FAILED
