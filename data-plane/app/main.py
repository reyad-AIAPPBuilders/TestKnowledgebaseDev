import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.dependencies.api_key import require_api_key
from app.middleware.hmac_auth import HMACAuthMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.routers.local import discover as local_discover
from app.routers.local import ingest as local_ingest
from app.routers.local import parse as local_parse
from app.routers.local import vectors as local_vectors
from app.routers.online import collections as online_collections
from app.routers.online import ingest as online_ingest
from app.routers.online import parse as online_parse
from app.routers.online import scrape as online_scrape
from app.routers.online import vectors as online_vectors
from app.routers.shared import classify, collections, health, metrics, search
from app.services.discovery.discovery_service import DiscoveryService
from app.services.discovery.r2_client import R2Client
from app.services.discovery.smb_client import SMBClient
from app.services.embedding.bge_m3_client import BGEM3Client
from app.services.embedding.openai_client import OpenAIEmbedClient
from app.services.embedding.qdrant_service import QdrantService
from app.services.ingest.ingest_service import IngestService
from app.services.intelligence.chunker import Chunker
from app.services.intelligence.classifier import Classifier
from app.services.intelligence.contextual import ContextualEnricher
from app.services.parsing.parser_service import ParserService
from app.services.scraping.scraper_service import ScraperService
from app.services.scraping.sitemap import SitemapParser
from app.services.search.search_service import SearchService
from app.utils.logger import get_logger, setup_logging

setup_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    app.state.start_time = time.monotonic()

    # Allow tests to inject fake services before TestClient startup
    if getattr(app.state, "_test_mode", False):
        log.info("app_started_test_mode")
        yield
        log.info("app_stopped_test_mode")
        return

    # ── Scraping ─────────────────────────────────────
    scraping_svc = ScraperService()
    await scraping_svc.startup()
    app.state.scraping = scraping_svc

    sitemap_parser = SitemapParser()
    app.state.sitemap_parser = sitemap_parser

    # ── Parsing ──────────────────────────────────────
    parser_svc = ParserService()
    await parser_svc.startup()
    app.state.parser = parser_svc

    # ── Intelligence ─────────────────────────────────
    classifier = Classifier()
    app.state.classifier = classifier

    contextual_enricher = ContextualEnricher()
    await contextual_enricher.startup()
    app.state.contextual_enricher = contextual_enricher

    # ── Embedding + Storage ──────────────────────────
    embedder = BGEM3Client()
    await embedder.startup()
    app.state.embedder = embedder

    openai_embedder = OpenAIEmbedClient()
    await openai_embedder.startup()
    app.state.openai_embedder = openai_embedder

    qdrant = QdrantService()
    await qdrant.startup()
    app.state.qdrant = qdrant

    # ── Discovery ────────────────────────────────────
    smb_client = SMBClient()
    r2_client = R2Client()
    await r2_client.startup()
    app.state.discovery = DiscoveryService(smb_client, r2_client)
    app.state.r2_client = r2_client

    # ── Ingest + Search ──────────────────────────────
    chunker = Chunker()
    app.state.ingest = IngestService(chunker, classifier, embedder, qdrant, contextual_enricher)
    app.state.online_ingest = IngestService(chunker, classifier, openai_embedder, qdrant, contextual_enricher)
    app.state.search = SearchService(openai_embedder, qdrant)

    log.info("app_started", mode=settings.mode, version=settings.version)
    yield

    # ── Shutdown ─────────────────────────────────────
    await scraping_svc.shutdown()
    await parser_svc.shutdown()
    await embedder.shutdown()
    await openai_embedder.shutdown()
    await contextual_enricher.shutdown()
    await qdrant.shutdown()
    await r2_client.shutdown()
    await sitemap_parser.close()

    log.info("app_stopped")


tags_metadata = [
    {
        "name": "Health",
        "description": "Liveness and readiness probes for container orchestrators and load balancers. "
        "The `/ready` endpoint checks connectivity to Qdrant, BGE-M3, Parser (LlamaParse or local), Crawl4AI, LDAP, and Redis.",
    },
    {
        "name": "Metrics",
        "description": "Prometheus-compatible metrics endpoint (`dp_` prefix).",
    },
    {
        "name": "Local - File Discovery",
        "description": "Scan SMB file shares or Cloudflare R2 buckets for new/changed documents. "
        "Returns file metadata, SHA-256 hashes, and NTFS ACLs for change detection.",
    },
    {
        "name": "Local - Document Parsing",
        "description": "Extract text, tables, and metadata from documents via file upload, SMB, or R2.\n\n"
        "**Input methods:**\n"
        "- `POST /local/document-parse` with `source: smb` — parse from mounted file share\n"
        "- `POST /local/document-parse` with `source: r2` — parse from Cloudflare R2 via presigned URL\n"
        "- `POST /local/document-parse/upload` — upload a file directly\n\n"
        "**Supported formats:** PDF, DOCX, DOC, PPTX, ODT, XLSX, XLS, TXT, CSV, HTML, RTF.\n\n"
        "**Parser backends** (auto-selected at startup):\n"
        "- **LlamaParse** (cloud) — `LLAMA_CLOUD_API_KEY` set → high-quality markdown extraction via LlamaCloud API\n"
        "- **Local parsers** (no API key) — PyMuPDF for PDF, python-docx for DOCX — lightweight, no heavy dependencies\n"
        "- **SpreadsheetParser** — always used for XLSX/XLS (openpyxl)\n"
        "- **TextParser** — always used for TXT, CSV, HTML, RTF",
    },
    {
        "name": "Local - Ingestion Pipeline",
        "description": "Full RAG ingestion pipeline for local documents: chunk → classify → embed (BGE-M3) → store (Qdrant).\n\n"
        "**Key features:**\n"
        "- Caller specifies the target `collection_name` (multi-tenant)\n"
        "- ACL-aware payloads with visibility-based permission filtering\n"
        "- Idempotent: re-ingesting the same `source_id` replaces old vectors automatically",
    },
    {
        "name": "Local - Vector Management",
        "description": "Delete vectors or update ACL permissions on existing vector points.\n\n"
        "- `DELETE /local/vectors/{source_id}?collection_name=...` — remove all vectors for a document\n"
        "- `POST /local/vectors/delete-by-filter` — remove vectors matching metadata filters\n"
        "- `PUT /local/vectors/update-acl` — update ACL payload on vectors without re-embedding",
    },
    {
        "name": "Online - Collection Management",
        "description": "List and inspect available Qdrant collections.\n\n"
        "**Requires `X-API-Key` header** when `DP_ONLINE_API_KEYS` is configured in `.env`.",
    },
    {
        "name": "Online - Web Scraping",
        "description": "Scrape webpages via Crawl4AI and discover URLs from sitemaps or BFS crawling.\n\n"
        "**Endpoints:**\n"
        "- `POST /online/scrape` — scrape a single webpage, optionally extract inner images and linked documents\n"
        "- `POST /online/crawl` — discover URLs via sitemap parsing or BFS link following\n\n"
        "**Requires `X-API-Key` header** when `DP_ONLINE_API_KEYS` is configured in `.env`.",
    },
    {
        "name": "Online - Document Parsing",
        "description": "Parse documents from public URLs or upload document files directly.\n\n"
        "**Input methods:**\n"
        "- `POST /online/document-parse` — parse from a public URL\n"
        "- `POST /online/document-parse/upload` — upload a file directly\n\n"
        "**Requires `X-API-Key` header** when `DP_ONLINE_API_KEYS` is configured in `.env`.",
    },
    {
        "name": "Online - Ingestion Pipeline",
        "description": "Full RAG ingestion pipeline for web-scraped content: chunk → classify → embed (OpenAI text-embedding-3-small) → store (Qdrant).\n\n"
        "**Key features:**\n"
        "- Chunking strategies: `contextual` (default, AI-enriched), `recursive`, `late_chunking`, `sentence`, `fixed`\n"
        "- Vector modes: `semantic` (dense only) or `hybrid` (dense + sparse BM25)\n"
        "- Idempotent: re-ingesting the same `source_id` replaces old vectors automatically\n\n"
        "**Requires `X-API-Key` header** when `DP_ONLINE_API_KEYS` is configured in `.env`.",
    },
    {
        "name": "Online - Vector Management",
        "description": "Delete vectors from online-ingested content.\n\n"
        "- `DELETE /online/vectors/{source_id}?collection_name=...` — remove all vectors for a document\n"
        "- `POST /online/vectors/delete-by-filter` — remove vectors matching metadata filters (AND logic)\n\n"
        "**Requires `X-API-Key` header** when `DP_ONLINE_API_KEYS` is configured in `.env`.",
    },
    {
        "name": "Content Intelligence",
        "description": "Classify municipality content into 9 categories (funding, event, policy, contact, form, announcement, minutes, report, general) "
        "and extract structured entities (dates, deadlines, monetary amounts, email contacts, departments).",
    },
    {
        "name": "Semantic Search",
        "description": "Permission-aware semantic and hybrid search across Qdrant collections.\n\n"
        "**Search modes:**\n"
        "- `semantic` (default) — dense-only cosine search via OpenAI `text-embedding-3-small`\n"
        "- `hybrid` — dense (OpenAI) + sparse (BM25) with Reciprocal Rank Fusion (RRF)\n\n"
        "**Key features:**\n"
        "- Caller specifies the target `collection_name` to search in\n"
        "- Mandatory user context for ACL filtering (citizen → public only; employee → public + internal with AD group intersection)\n"
        "- Results include `organization_id`, `department`, entity data, and content type\n"
        "- Optional filtering by content type (e.g. `funding`, `policy`)",
    },
    {
        "name": "Collection Management",
        "description": "Create and inspect Qdrant vector collections for municipality tenants.\n\n"
        "- `POST /collections/init` — create a new collection with configurable dense/sparse vector settings\n"
        "- `GET /collections/stats?collection_name=...` — get collection statistics (vector count, disk usage, classification breakdown)\n\n"
        "Each collection stores dense vectors (OpenAI or BGE-M3) and optional BM25 sparse vectors for hybrid search.",
    },
]

app = FastAPI(
    title="KI² Data Plane",
    description=(
        "Unified ingestion, embedding, and permission-aware search for municipality RAG pipelines.\n\n"
        "## Authentication\n\n"
        "| Method | Scope | How to enable |\n"
        "|--------|-------|---------------|\n"
        "| **HMAC-SHA256** | All endpoints (except `/health`, `/docs`) | Set `DP_HMAC_SECRET` in `.env` — clients send `X-Signature` + `X-Timestamp` headers |\n"
        "| **API Key** | Online endpoints only (`/api/v1/online/...`) | Set `DP_ONLINE_API_KEYS` in `.env` — clients send `X-API-Key` header |\n\n"
        "Both are disabled when their respective env vars are empty.\n\n"
        "## Two Operational Modes\n\n"
        "### 1. Online Mode — Knowledgebase from Web Content (`/api/v1/online/...`)\n"
        "Update the knowledgebase using online URLs and cloud services.\n"
        "- **Scrape** web pages via Crawl4AI, discover URLs from sitemaps or BFS crawling\n"
        "- **Parse** documents from any public URL or upload directly\n"
        "- **Ingest** scraped/parsed content into Qdrant with chunking, classification, and OpenAI embeddings\n"
        "- **Delete** vectors by source ID or metadata filters\n"
        "- Requires: `CRAWL4AI_URL`, `OPENAI_API_KEY`, `QDRANT_URL`\n\n"
        "### 2. Local Mode — On-Premise Document Processing (`/api/v1/local/...`)\n"
        "Process documents from SMB file shares or Cloudflare R2.\n"
        "- **Discover** files from SMB shares or R2 buckets with NTFS ACL extraction and change detection\n"
        "- **Parse** documents via upload, SMB, or R2 (LlamaParse cloud or local PyMuPDF/python-docx)\n"
        "- **Ingest** parsed content with ACL-aware payloads into Qdrant using BGE-M3 embeddings\n"
        "- **Manage** vectors: delete by source ID/filter, update ACL without re-embedding\n"
        "- Requires: `QDRANT_URL`, `BGE_M3_URL`\n\n"
        "## Pipeline Flow\n"
        "1. **Discover** → Scan file sources (SMB shares, R2) for new/changed documents\n"
        "2. **Scrape / Parse** → Extract text from web pages (Crawl4AI) or documents (URL, upload, SMB, R2)\n"
        "3. **Ingest** → Chunk, classify, embed (OpenAI / BGE-M3), and store in Qdrant with metadata\n"
        "4. **Search** → Permission-filtered semantic or hybrid search across collections\n"
    ),
    version=settings.version,
    lifespan=lifespan,
    openapi_tags=tags_metadata,
)

# Middleware (applied in reverse order — last added runs first)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(HMACAuthMiddleware)
app.add_middleware(RequestIDMiddleware)

# ── Shared Routers ────────────────────────────────────
app.include_router(health.router)
app.include_router(metrics.router)
app.include_router(classify.router)
app.include_router(collections.router)
app.include_router(search.router)

# ── Local Routers (no API key required) ───────────────
app.include_router(local_parse.router)
app.include_router(local_ingest.router)
app.include_router(local_discover.router)
app.include_router(local_vectors.router)

# ── Online Routers (API key required) ─────────────────
app.include_router(online_collections.router, dependencies=[Depends(require_api_key)])
app.include_router(online_scrape.router, dependencies=[Depends(require_api_key)])
app.include_router(online_parse.router, dependencies=[Depends(require_api_key)])
app.include_router(online_ingest.router, dependencies=[Depends(require_api_key)])
app.include_router(online_vectors.router, dependencies=[Depends(require_api_key)])
