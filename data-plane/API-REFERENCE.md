# KI² Data Plane — API Reference

Complete endpoint reference for the KI² Data Plane Service.

**Swagger UI:** `http://localhost:8000/docs`
**ReDoc:** `http://localhost:8000/redoc`
**OpenAPI JSON:** `http://localhost:8000/openapi.json`

---

## Base URL

```
http://localhost:8000/api/v1
```

- On-premise: `http://{vm-ip}:8000/api/v1`
- Cloud: `https://your-coolify-domain/api/v1`

---

## Two Operational Modes

### 1. Online Mode — Knowledgebase from Web Content
Update the knowledgebase using online URLs and cloud services.
- Scrape web pages via Crawl4AI
- Parse documents from any public URL — uses LlamaParse (cloud)
- All online endpoints live under `/api/v1/online/`
- Requires: `CRAWL4AI_URL`, `LLAMA_CLOUD_API_KEY`, `OPENAI_API_KEY`

### 2. Local Mode — Fully Offline Document Processing
Process documents entirely locally without any third-party APIs.
- Upload documents via `POST /api/v1/local/parse/upload` or read from SMB file shares
- Parse locally with PyMuPDF (PDF) + python-docx (DOCX) — lightweight, no GPU needed
- All local endpoints live under `/api/v1/local/`
- Requires: Only Qdrant + BGE-M3

---

## Authentication

### HMAC Authentication (global)

All endpoints except `/health` and `/ready` require HMAC-SHA256 authentication (when `DP_HMAC_SECRET` is set).

| Header | Description |
|--------|-------------|
| `X-Signature` | HMAC-SHA256 of `{timestamp}.{request_body}` |
| `X-Timestamp` | Unix epoch seconds (must be within ±5 min) |

Leave `DP_HMAC_SECRET` empty to disable HMAC authentication.

### API Key Authentication (online endpoints only)

All `/api/v1/online/*` endpoints require an `X-API-Key` header. Valid keys are configured via the `DP_ONLINE_API_KEYS` environment variable.

| Header | Description |
|--------|-------------|
| `X-API-Key` | API key for online endpoint access (configured via `DP_ONLINE_API_KEYS` env var) |

Local endpoints (`/api/v1/local/*`) do **not** require an API key — they are designed for trusted network access.

> **Note:** If both `DP_HMAC_SECRET` and `DP_ONLINE_API_KEYS` are set, online endpoints require **both** HMAC and API key headers.

---

## Response Envelope

Every API response is wrapped in a standard envelope:

**Success:**
```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "detail": null,
  "request_id": "ca60c30a-6289-4732-9b9f-028d207bb9a1"
}
```

**Error:**
```json
{
  "success": false,
  "data": null,
  "error": "PARSE_FAILED",
  "detail": "Human-readable error message",
  "request_id": "ca60c30a-6289-4732-9b9f-028d207bb9a1"
}
```

All responses include an `X-Request-ID` header. Send `X-Request-ID` in your request to trace it through.

---

# Shared Endpoints

These endpoints are not scoped to online or local mode.

## `GET /api/v1/health`

Liveness check. No authentication required.

**Response:**
```json
{
  "status": "ok",
  "uptime_seconds": 3421.5
}
```

---

## `GET /api/v1/ready`

Readiness check. Returns minimal response without auth, full response with HMAC auth.

**Minimal response (no auth):**
```json
{
  "ready": true,
  "uptime_seconds": 3421.5
}
```

**Full response (with HMAC auth headers):**
```json
{
  "ready": true,
  "services": {
    "qdrant": true,
    "bge_m3": true,
    "parser": true,
    "crawl4ai": true,
    "ldap": false,
    "redis": true
  },
  "mode": "on-premise",
  "tenant_id": "wiener-neudorf",
  "worker_id": "wn-worker-01",
  "version": "1.0.0",
  "uptime_seconds": 3421.5
}
```

Core services required for `ready: true`: **qdrant**, **bge_m3**, **parser**, **crawl4ai**.

---

## `GET /metrics`

Prometheus-compatible metrics with `dp_` prefix. Returns `text/plain`.

---

## `POST /api/v1/classify`

Classify content into 9 categories and extract structured entities. Designed for German-language municipality documents.

**Categories:** `funding`, `event`, `policy`, `contact`, `form`, `announcement`, `minutes`, `report`, `general`

**Request:**
```json
{
  "content": "Das Förderprogramm für erneuerbare Energien gilt ab 01.04.2025. Antragsfrist bis 30.06.2025. Förderhöhe bis EUR 5.000. Kontakt: energie@wiener-neudorf.gv.at, Umweltamt.",
  "language": "de"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "classification": "funding",
    "confidence": 0.95,
    "sub_categories": ["renewable_energy"],
    "entities": {
      "dates": ["01.04.2025", "30.06.2025"],
      "deadlines": ["30.06.2025"],
      "amounts": ["EUR 5.000"],
      "contacts": ["energie@wiener-neudorf.gv.at"],
      "departments": ["Umweltamt"]
    },
    "summary": "Förderung für erneuerbare Energien, Antragsfrist bis 30. Juni 2025"
  },
  "request_id": "..."
}
```

**Response (empty content):**
```json
{
  "success": false,
  "error": "VALIDATION_EMPTY_CONTENT",
  "detail": "Content must not be empty",
  "request_id": "..."
}
```

### Request fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `content` | string | Yes | — | Text to classify (from `/online/parse`, `/local/parse`, or `/online/scrape`) |
| `language` | string | No | `de` | ISO 639-1 language code |

### Extracted entities

| Entity | Examples |
|--------|----------|
| `dates` | `01.04.2025`, `2025-06-30` |
| `deadlines` | Dates near words like "Frist", "bis", "deadline" |
| `amounts` | `EUR 5.000`, `€ 10.000` |
| `contacts` | Email addresses |
| `departments` | `Umweltamt`, `Bauamt`, `Finanzabteilung` |

### Error codes
`VALIDATION_EMPTY_CONTENT`, `CLASSIFY_FAILED`, `CLASSIFY_LOW_CONFIDENCE`, `ENTITY_EXTRACTION_FAILED`

---

## `POST /api/v1/search`

Permission-aware semantic search. **No search is ever unfiltered** — every request requires a user context.

**Permission model:**
- `citizen` → sees only `visibility: "public"` documents
- `employee` → sees `public` + `internal`, filtered by AD group membership

### Case 1: Employee search with classification filter

**Request:**
```json
{
  "collection_name": "wiener-neudorf",
  "query": "Wann ist die nächste Förderung für Solaranlagen?",
  "user": {
    "type": "employee",
    "user_id": "maria@wiener-neudorf.gv.at",
    "groups": ["DOMAIN\\Bauamt-Mitarbeiter", "DOMAIN\\Alle-Mitarbeiter"],
    "roles": ["member"],
    "department": "bauamt"
  },
  "filters": {
    "content_type": ["funding"]
  },
  "top_k": 10,
  "score_threshold": 0.5
}
```

### Case 2: Citizen search (public documents only)

**Request:**
```json
{
  "collection_name": "wiener-neudorf",
  "query": "Öffnungszeiten Gemeindeamt",
  "user": {
    "type": "citizen",
    "user_id": "anonymous"
  },
  "top_k": 5,
  "score_threshold": 0.5
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "results": [
      {
        "chunk_id": "doc_abc123_chunk_0007",
        "source_id": "doc_abc123",
        "chunk_text": "Die Förderung für Solaranlagen beträgt bis zu EUR 5.000...",
        "score": 0.92,
        "source_path": "//server/bauamt/foerderungen/solar_2025.pdf",
        "content_type": ["funding", "renewable_energy"],
        "entities": {
          "amounts": ["EUR 5.000"],
          "deadlines": ["2025-06-30"]
        },
        "metadata": {
          "title": "Solarförderung 2025",
          "organization_id": "org_wiener_neudorf",
          "department": "bauamt",
          "source_type": "smb"
        }
      }
    ],
    "total_results": 7,
    "query_embedding_ms": 15,
    "search_ms": 22,
    "permission_filter_applied": {
      "visibility": ["public", "internal"],
      "must_match_groups": ["DOMAIN\\Bauamt-Mitarbeiter", "DOMAIN\\Alle-Mitarbeiter"],
      "must_not_match_groups": []
    }
  },
  "request_id": "..."
}
```

**Response (no results):**
```json
{
  "success": true,
  "data": {
    "results": [],
    "total_results": 0,
    "query_embedding_ms": 12,
    "search_ms": 5,
    "permission_filter_applied": {
      "visibility": ["public"],
      "must_match_groups": [],
      "must_not_match_groups": []
    }
  },
  "request_id": "..."
}
```

**Response (empty query):**
```json
{
  "success": false,
  "error": "VALIDATION_EMPTY_CONTENT",
  "detail": "Query must not be empty",
  "request_id": "..."
}
```

### Request fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `collection_name` | string | Yes | — | Qdrant collection to search |
| `query` | string | Yes | — | Natural language search query |
| `user` | object | Yes | — | User identity (always required) |
| `user.type` | string | Yes | — | `citizen` or `employee` |
| `user.user_id` | string | Yes | — | Email, AD username, or `anonymous` |
| `user.groups` | array | No | `[]` | AD groups (required for employees) |
| `user.roles` | array | No | `[]` | Portal roles |
| `user.department` | string | No | — | Department for filtering |
| `filters` | object | No | — | Optional content filters |
| `filters.content_type` | array | No | — | Filter by content types (e.g. `["funding", "policy"]`) |
| `top_k` | int | No | 10 | Max results (1-100) |
| `score_threshold` | float | No | 0.5 | Min similarity score (0.0-1.0) |

### Error codes
`VALIDATION_USER_REQUIRED`, `VALIDATION_EMPTY_CONTENT`, `EMBEDDING_MODEL_NOT_LOADED`, `EMBEDDING_FAILED`, `QDRANT_CONNECTION_FAILED`, `QDRANT_COLLECTION_NOT_FOUND`, `QDRANT_SEARCH_FAILED`

---

## `POST /api/v1/collections/init`

Create a Qdrant vector collection for a municipality tenant. If the collection already exists, returns `created: false` without error.

**Request (default config):**
```json
{
  "collection_name": "wiener-neudorf"
}
```

**Request (custom config):**
```json
{
  "collection_name": "wiener-neudorf",
  "vector_config": {
    "dense_dim": 1024,
    "sparse": true,
    "distance": "cosine"
  }
}
```

**Response (newly created):**
```json
{
  "success": true,
  "data": {
    "collection": "wiener-neudorf",
    "created": true,
    "dense_dim": 1024,
    "sparse_enabled": true
  },
  "request_id": "..."
}
```

**Response (already exists):**
```json
{
  "success": true,
  "data": {
    "collection": "wiener-neudorf",
    "created": false,
    "dense_dim": 1024,
    "sparse_enabled": true
  },
  "request_id": "..."
}
```

### Error codes
`QDRANT_CONNECTION_FAILED`

---

## `GET /api/v1/collections/stats`

Get statistics for a Qdrant collection.

**Request:**
```
GET /api/v1/collections/stats?collection_name=wiener-neudorf
```

**Response:**
```json
{
  "success": true,
  "data": {
    "collection": "wiener-neudorf",
    "total_vectors": 12450,
    "total_documents": 0,
    "disk_usage_mb": 245.5,
    "by_classification": {},
    "by_visibility": {}
  },
  "request_id": "..."
}
```

**Response (collection not found):**
```json
{
  "success": false,
  "error": "QDRANT_COLLECTION_NOT_FOUND",
  "detail": "Collection 'nonexistent' not found",
  "request_id": "..."
}
```

### Error codes
`QDRANT_COLLECTION_NOT_FOUND`, `QDRANT_CONNECTION_FAILED`

---

# Online Endpoints

All online endpoints require the `X-API-Key` header (configured via `DP_ONLINE_API_KEYS` env var). These endpoints handle web content and cloud-based document processing.

## `POST /api/v1/online/scrape`

Scrape a single webpage using Crawl4AI with JavaScript rendering. Results are cached in Redis.

**Request:**
```bash
curl -X POST "https://your-domain/api/v1/online/scrape" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "url": "https://www.wiener-neudorf.gv.at/foerderungen"
  }'
```

**Request body:**
```json
{
  "url": "https://www.wiener-neudorf.gv.at/foerderungen"
}
```

**Response (success):**
```json
{
  "success": true,
  "data": {
    "url": "https://www.wiener-neudorf.gv.at/foerderungen",
    "title": "Förderungen - Gemeinde Wiener Neudorf",
    "content": "# Förderungen\n\nDie Gemeinde Wiener Neudorf bietet folgende Förderungen an...",
    "content_length": 5200,
    "language": "de",
    "links_found": 45,
    "last_modified": null
  },
  "request_id": "..."
}
```

**Response (invalid URL):**
```json
{
  "success": false,
  "error": "VALIDATION_URL_INVALID",
  "detail": "URL must start with http:// or https://",
  "request_id": "..."
}
```

**Response (empty page):**
```json
{
  "success": false,
  "error": "SCRAPE_EMPTY",
  "detail": "Page returned no extractable content",
  "request_id": "..."
}
```

**Response (timeout):**
```json
{
  "success": false,
  "error": "SCRAPE_TIMEOUT",
  "detail": "Request timed out after 30s",
  "request_id": "..."
}
```

### Error codes
`VALIDATION_URL_INVALID`, `SCRAPE_FAILED`, `SCRAPE_BLOCKED`, `SCRAPE_TIMEOUT`, `SCRAPE_EMPTY`, `SCRAPE_ROBOTS_BLOCKED`

---

## `POST /api/v1/online/crawl`

Discover URLs from a website. Returns URLs only — does not scrape content.

### Case 1: Sitemap discovery

**Request:**
```bash
curl -X POST "https://your-domain/api/v1/online/crawl" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "url": "https://www.wiener-neudorf.gv.at/sitemap.xml",
    "method": "sitemap",
    "max_urls": 500
  }'
```

### Case 2: BFS crawl discovery

**Request:**
```json
{
  "url": "https://www.wiener-neudorf.gv.at",
  "method": "crawl",
  "max_depth": 3,
  "max_urls": 100
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "base_url": "https://www.wiener-neudorf.gv.at",
    "method_used": "sitemap",
    "total_urls": 234,
    "urls": [
      {
        "url": "https://www.wiener-neudorf.gv.at/gemeindeamt/kontakt/",
        "type": "page",
        "last_modified": null
      },
      {
        "url": "https://www.wiener-neudorf.gv.at/files/foerderung.pdf",
        "type": "document",
        "last_modified": null
      }
    ]
  },
  "request_id": "..."
}
```

**Response (no sitemap found):**
```json
{
  "success": false,
  "error": "CRAWL_SITEMAP_NOT_FOUND",
  "detail": "No URLs found in sitemap",
  "request_id": "..."
}
```

### Request fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | Yes | — | Base URL or sitemap URL |
| `method` | string | Yes | — | `sitemap` or `crawl` |
| `max_depth` | int | No | 3 | Max link-following depth (1-5) |
| `max_urls` | int | No | 500 | Max URLs to return (1-5000) |

### Error codes
`VALIDATION_URL_INVALID`, `CRAWL_SITEMAP_NOT_FOUND`

---

## `POST /api/v1/online/parse`

Parse a document from a public URL. Uses LlamaParse (cloud) when `LLAMA_CLOUD_API_KEY` is set, otherwise falls back to local parsers.

**Supported formats:** PDF, DOCX, DOC, PPTX, ODT, XLSX, XLS, TXT, CSV, HTML, RTF

**Request:**
```bash
curl -X POST "https://your-domain/api/v1/online/parse" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "url": "https://pdfobject.com/pdf/sample.pdf"
  }'
```

**Request body:**
```json
{
  "url": "https://pdfobject.com/pdf/sample.pdf"
}
```

`mime_type` is optional — auto-detected from the URL.

**Request with explicit MIME type:**
```json
{
  "url": "https://example.com/download?id=123",
  "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "file_path": "https://pdfobject.com/pdf/sample.pdf",
    "content": "This is a simple PDF file. Fun fun fun...",
    "pages": 2,
    "language": "en",
    "extracted_tables": 0,
    "content_length": 1234
  },
  "request_id": "5786ede5-7631-46f2-8e6b-0c48f8564274"
}
```

### Request fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | Yes | Public URL of the document to parse |
| `mime_type` | string | No | MIME type. Auto-detected from URL if omitted. |

### Error codes
`PARSE_FAILED`, `PARSE_ENCRYPTED`, `PARSE_CORRUPTED`, `PARSE_EMPTY`, `PARSE_TIMEOUT`, `PARSE_UNSUPPORTED_FORMAT`

---

## `POST /api/v1/online/ingest`

The RAG pipeline endpoint for web content. Takes parsed text and runs: **chunk -> classify -> embed (OpenAI text-embedding-3-small) -> store (Qdrant)**.

Uses `url` instead of `file_path` to identify the source. The `url` is automatically stored as `source_url` in every Qdrant point's metadata.

The collection is **auto-created** if it does not exist, using the specified `vector_config` settings.

**Vector modes** (via `vector_config.search_mode`):
- `semantic` (default) — stores only dense cosine vectors. Best for pure semantic similarity search.
- `hybrid` — stores both dense cosine vectors **and** sparse vectors. Enables combined semantic + lexical (BM25-style) search for higher recall.

**Request (semantic mode — default):**
```bash
curl -X POST "https://your-domain/api/v1/online/ingest" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "collection_name": "wiener-neudorf",
    "source_id": "web_foerderungen_001",
    "url": "https://www.wiener-neudorf.gv.at/foerderungen",
    "content": "Die Gemeinde Wiener Neudorf bietet folgende Förderungen an...",
    "language": "de",
    "metadata": {
      "assistant_id": "asst_wiener_neudorf_01",
      "title": "Förderungen - Gemeinde Wiener Neudorf",
      "source_type": "web",
      "organization_id": "org_wiener_neudorf",
      "department": ["Bürgerservice", "Förderungen"]
    }
  }'
```

**Request (hybrid mode with custom vector size):**
```bash
curl -X POST "https://your-domain/api/v1/online/ingest" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "collection_name": "wiener-neudorf",
    "source_id": "web_foerderungen_001",
    "url": "https://www.wiener-neudorf.gv.at/foerderungen",
    "content": "Die Gemeinde Wiener Neudorf bietet folgende Förderungen an...",
    "language": "de",
    "metadata": {
      "assistant_id": "asst_wiener_neudorf_01",
      "title": "Förderungen - Gemeinde Wiener Neudorf",
      "source_type": "web",
      "organization_id": "org_wiener_neudorf",
      "department": ["Bürgerservice"]
    },
    "vector_config": {
      "vector_size": 1536,
      "search_mode": "hybrid"
    }
  }'
```

**Response (success):**
```json
{
  "success": true,
  "data": {
    "source_id": "web_foerderungen_001",
    "chunks_created": 4,
    "vectors_stored": 4,
    "collection": "wiener-neudorf",
    "content_type": ["funding", "renewable_energy"],
    "entities_extracted": {
      "dates": 2,
      "contacts": 1,
      "amounts": 1
    },
    "embedding_time_ms": 850,
    "total_time_ms": 2100
  },
  "request_id": "..."
}
```

### Request fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `collection_name` | string | Yes | — | Qdrant collection to store in (auto-created if missing) |
| `source_id` | string | Yes | — | Unique document ID (for updates/deletes) |
| `url` | string | Yes | — | Source URL — stored as `source_url` in Qdrant point metadata |
| `content` | string | Yes | — | Parsed text from `/online/scrape` or `/online/parse` |
| `language` | string | No | auto-detect | ISO 639-1 language code |
| `metadata` | object | Yes | — | Document metadata (see Online Metadata object below) |
| `chunking` | object | No | defaults | Chunking configuration (see Chunking config below) |
| `vector_config` | object | No | defaults | Vector storage settings (see Vector config below) |

### Online Metadata object

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `assistant_id` | string | Yes | — | Identifier of the assistant that owns this content |
| `title` | string | No | `null` | Document/page title (shown in search results) |
| `uploaded_by` | string | No | `null` | User or service that triggered ingestion |
| `source_type` | string | No | `"web"` | Origin type (typically `web` for online content) |
| `mime_type` | string | No | `null` | Original content MIME type |
| `organization_id` | string | No | `null` | Organization/tenant identifier |
| `department` | array of strings | No | `[]` | Departments within the organization |

### Vector config

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `vector_size` | int | `1536` | Dimensionality of the dense cosine vector (64–4096). Must match embedding model output. |
| `search_mode` | string | `"semantic"` | `"semantic"` — dense vectors only. `"hybrid"` — dense + sparse vectors for combined semantic + lexical search. |

### Qdrant point payload structure

Each Qdrant point has two top-level payload fields: `content` and `metadata`.

```json
{
  "id": "uuid",
  "vector": {"dense": [...]},
  "payload": {
    "content": "The chunk text content...",
    "metadata": {
      "chunk_id": "web_foerderungen_001_chunk_0000",
      "source_id": "web_foerderungen_001",
      "chunk_index": 0,
      "source_url": "https://www.wiener-neudorf.gv.at/foerderungen",
      "source_path": "https://www.wiener-neudorf.gv.at/foerderungen",
      "content_type": ["funding", "renewable_energy"],
      "language": "de",
      "assistant_id": "asst_wiener_neudorf_01",
      "title": "Förderungen - Gemeinde Wiener Neudorf",
      "source_type": "web",
      "mime_type": "text/html",
      "uploaded_by": "scraper",
      "organization_id": "org_wiener_neudorf",
      "department": ["Bürgerservice", "Förderungen"]
    }
  }
}
```

| Field | Location | Description |
|-------|----------|-------------|
| `content` | `payload.content` | The text content of this chunk |
| `chunk_id` | `payload.metadata` | `{source_id}_chunk_{index}` |
| `source_id` | `payload.metadata` | Document identifier |
| `chunk_index` | `payload.metadata` | Position of chunk within document |
| `source_url` | `payload.metadata` | Source URL from request |
| `source_path` | `payload.metadata` | Original source path/URL |
| `content_type` | `payload.metadata` | Auto-detected content categories (array of strings) |
| `language` | `payload.metadata` | ISO 639-1 language code |
| `assistant_id` | `payload.metadata` | Assistant identifier |
| `title` | `payload.metadata` | Document title |
| `source_type` | `payload.metadata` | Origin type |
| `mime_type` | `payload.metadata` | MIME type |
| `uploaded_by` | `payload.metadata` | Uploader identity |
| `organization_id` | `payload.metadata` | Organization/tenant ID |
| `department` | `payload.metadata` | Departments (array of strings) |

### Error codes
`VALIDATION_EMPTY_CONTENT`, `EMBEDDING_MODEL_NOT_LOADED`, `EMBEDDING_FAILED`, `EMBEDDING_OOM`, `QDRANT_CONNECTION_FAILED`, `QDRANT_COLLECTION_NOT_FOUND`, `QDRANT_UPSERT_FAILED`, `QDRANT_DISK_FULL`, `CLASSIFY_FAILED`

---

# Local Endpoints

Local endpoints do **not** require an `X-API-Key` header. They are designed for trusted network environments (on-premise, internal network).

## `POST /api/v1/local/parse`

Parse a document from an SMB file share or Cloudflare R2 bucket. Uses local parsers: PyMuPDF for PDF, python-docx for DOCX, SpreadsheetParser for XLSX/XLS, TextParser for TXT/CSV/HTML/RTF.

**Supported formats:** PDF, DOCX, DOC, PPTX, ODT, XLSX, XLS, TXT, CSV, HTML, RTF

### Case 1: Parse from SMB file share

**Request:**
```bash
curl -X POST "https://your-domain/api/v1/local/parse" \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "//server/bauamt/dokumente/antrag_001.pdf",
    "source": "smb",
    "mime_type": "application/pdf"
  }'
```

**Response:**
```json
{
  "success": true,
  "data": {
    "file_path": "//server/bauamt/dokumente/antrag_001.pdf",
    "content": "Bauantrag Nr. 2024-001\nAntragsteller: Max Mustermann...",
    "pages": 12,
    "language": "de",
    "extracted_tables": 2,
    "content_length": 15420
  },
  "request_id": "..."
}
```

### Case 2: Parse from Cloudflare R2

**Request:**
```json
{
  "file_path": "tenant/wiener-neudorf/uploads/report.docx",
  "source": "r2",
  "r2_presigned_url": "https://r2.example.com/presigned/report.docx?token=abc123",
  "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}
```

### Parse error examples

**Encrypted PDF:**
```json
{
  "success": false,
  "data": null,
  "error": "PARSE_ENCRYPTED",
  "detail": "Parser error: encrypted PDF requires password",
  "request_id": "..."
}
```

**Empty document:**
```json
{
  "success": false,
  "data": null,
  "error": "PARSE_EMPTY",
  "detail": "Document contained no extractable text",
  "request_id": "..."
}
```

**Unsupported format:**
```json
{
  "success": false,
  "data": null,
  "error": "PARSE_UNSUPPORTED_FORMAT",
  "detail": "Unsupported document type: unknown",
  "request_id": "..."
}
```

**R2 missing presigned URL:**
```json
{
  "success": false,
  "data": null,
  "error": "R2_FILE_NOT_FOUND",
  "detail": "r2_presigned_url is required when source is r2",
  "request_id": "..."
}
```

### Request fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file_path` | string | Yes | SMB path or R2 object key |
| `source` | string | Yes | `smb` or `r2` |
| `mime_type` | string | No | MIME type (recommended for SMB/R2) |
| `r2_presigned_url` | string | Conditional | Required when `source` is `r2` |

### Error codes
`PARSE_FAILED`, `PARSE_ENCRYPTED`, `PARSE_CORRUPTED`, `PARSE_EMPTY`, `PARSE_TIMEOUT`, `PARSE_UNSUPPORTED_FORMAT`, `R2_FILE_NOT_FOUND`

---

## `POST /api/v1/local/parse/upload`

Upload a document file directly for parsing. Uses `multipart/form-data`.

**Request (cURL):**
```bash
curl -X POST "https://your-domain/api/v1/local/parse/upload" \
  -F "file=@/path/to/document.pdf"
```

**Request (Swagger UI):** Click "Try it out", choose a file, and execute.

**Response:**
```json
{
  "success": true,
  "data": {
    "file_path": "document.pdf",
    "content": "Extracted text content from the uploaded PDF...",
    "pages": 5,
    "language": "de",
    "extracted_tables": 1,
    "content_length": 8500
  },
  "request_id": "..."
}
```

---

## `POST /api/v1/local/discover`

Scan SMB file shares or R2 buckets for new/changed documents. First step in every ingestion pipeline — does NOT parse or embed. Returns NTFS ACLs, SHA-256 hashes, and change status.

### Case 1: SMB file share scan

**Request:**
```bash
curl -X POST "https://your-domain/api/v1/local/discover" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "smb",
    "paths": ["//server/abteilung/dokumente", "//server/bauamt"],
    "since_hash_map": {
      "//server/abteilung/dokumente/antrag.pdf": "sha256:abc123def456..."
    }
  }'
```

### Case 2: R2 bucket scan

**Request:**
```json
{
  "source": "r2",
  "paths": ["tenant/wiener-neudorf/uploads/"],
  "since_hash_map": {}
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "total_files": 523,
    "new_files": 12,
    "changed_files": 3,
    "unchanged_files": 508,
    "files": [
      {
        "path": "//server/bauamt/antrag_001.pdf",
        "file_hash": "sha256:abc123...",
        "size_bytes": 245000,
        "mime_type": "application/pdf",
        "last_modified": "2025-03-01T10:30:00Z",
        "status": "new",
        "acl": {
          "source": "ntfs",
          "allow_groups": ["DOMAIN\\Bauamt-Mitarbeiter"],
          "deny_groups": ["DOMAIN\\Praktikanten"],
          "allow_users": [],
          "inherited": true
        }
      }
    ]
  },
  "request_id": "..."
}
```

**Response (path not found):**
```json
{
  "success": false,
  "error": "SMB_PATH_NOT_FOUND",
  "detail": "Share path //server/invalid not found",
  "request_id": "..."
}
```

### Request fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | Yes | `smb`, `r2`, or `url` |
| `paths` | array | Yes | SMB paths, R2 prefixes, or URLs to scan |
| `since_hash_map` | object | No | `{file_path: last_known_hash}` — matching hashes are skipped |

### Error codes
`SMB_CONNECTION_FAILED`, `SMB_AUTH_FAILED`, `SMB_PATH_NOT_FOUND`, `R2_CONNECTION_FAILED`, `R2_FILE_NOT_FOUND`, `LDAP_CONNECTION_FAILED`, `VALIDATION_PATH_OUTSIDE_ROOTS`

---

## `POST /api/v1/local/ingest`

The core RAG pipeline endpoint for local documents. Takes parsed text + ACL and runs: **chunk -> classify -> embed (BGE-M3) -> store (Qdrant)**.

- Multi-tenant: specify `collection_name`
- Idempotent: re-ingesting the same `source_id` replaces old vectors
- Every document MUST have an ACL with `visibility` set

**Request:**
```bash
curl -X POST "https://your-domain/api/v1/local/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "collection_name": "wiener-neudorf",
    "source_id": "doc_abc123",
    "file_path": "//server/bauamt/antrag_001.pdf",
    "content": "Bauantrag Nr. 2024-001\nAntragsteller: Max Mustermann\n\nDer Antrag auf Errichtung eines Einfamilienhauses...",
    "language": "de",
    "acl": {
      "allow_groups": ["DOMAIN\\Bauamt-Mitarbeiter"],
      "deny_groups": ["DOMAIN\\Praktikanten"],
      "allow_roles": [],
      "allow_users": [],
      "department": "bauamt",
      "visibility": "internal"
    },
    "metadata": {
      "title": "Bauantrag 2024-001",
      "uploaded_by": "moderator_01",
      "source_type": "smb",
      "mime_type": "application/pdf",
      "organization_id": "org_wiener_neudorf",
      "department": "bauamt"
    },
    "chunking": {
      "strategy": "late_chunking",
      "max_chunk_size": 512,
      "overlap": 50
    }
  }'
```

**Response (success):**
```json
{
  "success": true,
  "data": {
    "source_id": "doc_abc123",
    "chunks_created": 8,
    "vectors_stored": 8,
    "collection": "wiener-neudorf",
    "content_type": ["policy", "housing"],
    "entities_extracted": {
      "dates": 3,
      "contacts": 1,
      "amounts": 0
    },
    "embedding_time_ms": 1250,
    "total_time_ms": 3500
  },
  "request_id": "..."
}
```

**Response (empty content):**
```json
{
  "success": false,
  "error": "VALIDATION_EMPTY_CONTENT",
  "detail": "Content must not be empty",
  "request_id": "..."
}
```

**Response (embedding OOM):**
```json
{
  "success": false,
  "error": "EMBEDDING_OOM",
  "detail": "BGE-M3 out of memory — reduce chunk size or content length",
  "request_id": "..."
}
```

**Response (collection not found):**
```json
{
  "success": false,
  "error": "QDRANT_COLLECTION_NOT_FOUND",
  "detail": "Collection 'wiener-neudorf' does not exist. Create it first via POST /collections/init",
  "request_id": "..."
}
```

### Request fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `collection_name` | string | Yes | — | Qdrant collection to store in |
| `source_id` | string | Yes | — | Unique document ID (for updates/deletes) |
| `file_path` | string | Yes | — | Original file path (SMB path or R2 key) |
| `content` | string | Yes | — | Parsed text from `/local/parse` or `/local/parse/upload` |
| `language` | string | No | auto-detect | ISO 639-1 language code |
| `acl` | object | Yes | — | Access control list (see ACL object below) |
| `metadata` | object | Yes | — | Document metadata (see Metadata object below) |
| `chunking` | object | No | defaults | Chunking configuration (see Chunking config below) |

### ACL object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `visibility` | string | Yes | `public`, `internal`, or `restricted` |
| `allow_groups` | array | No | AD groups with access |
| `deny_groups` | array | No | AD groups explicitly denied |
| `allow_roles` | array | No | Portal roles with access |
| `allow_users` | array | No | Specific user IDs |
| `department` | string | No | Department tag |

### Metadata object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | No | Document title (shown in search results) |
| `uploaded_by` | string | No | User or service that uploaded |
| `source_type` | string | No | `smb`, `r2`, or `web` |
| `mime_type` | string | No | Original file MIME type |
| `organization_id` | string | No | Organization/tenant ID |
| `department` | string | No | Department within organization |

### Chunking config

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `strategy` | string | `late_chunking` | `late_chunking` (paragraph-aware), `sentence`, or `fixed` |
| `max_chunk_size` | int | 512 | Max chunk size in chars (64-4096) |
| `overlap` | int | 50 | Overlap between chunks in chars (0-512) |

### Error codes
`VALIDATION_EMPTY_CONTENT`, `VALIDATION_ACL_REQUIRED`, `EMBEDDING_MODEL_NOT_LOADED`, `EMBEDDING_FAILED`, `EMBEDDING_OOM`, `QDRANT_CONNECTION_FAILED`, `QDRANT_COLLECTION_NOT_FOUND`, `QDRANT_UPSERT_FAILED`, `QDRANT_DISK_FULL`, `CLASSIFY_FAILED`

---

## `DELETE /api/v1/local/vectors/{source_id}`

Remove all vectors for a document from a Qdrant collection.

**Request:**
```bash
curl -X DELETE "https://your-domain/api/v1/local/vectors/doc_abc123?collection_name=wiener-neudorf"
```

**Response:**
```json
{
  "success": true,
  "data": {
    "source_id": "doc_abc123",
    "vectors_deleted": 8
  },
  "request_id": "..."
}
```

**Response (connection failed):**
```json
{
  "success": false,
  "error": "QDRANT_CONNECTION_FAILED",
  "detail": "Failed to connect to Qdrant",
  "request_id": "..."
}
```

### Error codes
`QDRANT_CONNECTION_FAILED`, `QDRANT_DELETE_FAILED`

---

## `POST /api/v1/local/vectors/delete-by-filter`

Delete vectors matching metadata filters. All filters are combined with **AND** logic — only points matching every condition are deleted.

**Filterable metadata fields:** `source_id`, `source_type`, `content_type`, `acl_visibility`, `acl_department`, `organization_id`, `department`, `language`, `uploaded_by`, `mime_type`, `title`

### Case 1: Delete all vectors from a department

**Request:**
```bash
curl -X POST "https://your-domain/api/v1/local/vectors/delete-by-filter" \
  -H "Content-Type: application/json" \
  -d '{
    "collection_name": "wiener-neudorf",
    "filters": [
      {"key": "acl_department", "value": "bauamt"}
    ]
  }'
```

### Case 2: Delete by source type and content type

**Request:**
```json
{
  "collection_name": "wiener-neudorf",
  "filters": [
    {"key": "source_type", "value": "smb"},
    {"key": "content_type", "value": "funding"}
  ]
}
```

### Case 3: Delete all vectors for an organization

**Request:**
```json
{
  "collection_name": "wiener-neudorf",
  "filters": [
    {"key": "organization_id", "value": "org_wiener_neudorf"}
  ]
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "vectors_deleted": 42,
    "filters_applied": [
      {"key": "source_type", "value": "smb"},
      {"key": "content_type", "value": "funding"}
    ]
  },
  "request_id": "..."
}
```

**Response (connection failed):**
```json
{
  "success": false,
  "error": "QDRANT_CONNECTION_FAILED",
  "detail": "Failed to connect to Qdrant",
  "request_id": "..."
}
```

### Request fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `collection_name` | string | Yes | Qdrant collection to delete from |
| `filters` | array | Yes | List of metadata conditions (AND logic, min 1) |
| `filters[].key` | string | Yes | Metadata field name |
| `filters[].value` | string | Yes | Exact value to match |

### Error codes
`QDRANT_CONNECTION_FAILED`, `QDRANT_DELETE_FAILED`

---

## `PUT /api/v1/local/vectors/update-acl`

Update ACL permissions on existing vectors without re-embedding. Used when file permissions change on the source system.

**Request:**
```bash
curl -X PUT "https://your-domain/api/v1/local/vectors/update-acl" \
  -H "Content-Type: application/json" \
  -d '{
    "collection_name": "wiener-neudorf",
    "source_id": "doc_abc123",
    "acl": {
      "allow_groups": ["DOMAIN\\Bauamt-Mitarbeiter", "DOMAIN\\Neue-Gruppe"],
      "deny_groups": [],
      "allow_roles": [],
      "allow_users": [],
      "department": "bauamt",
      "visibility": "internal"
    }
  }'
```

**Response:**
```json
{
  "success": true,
  "data": {
    "source_id": "doc_abc123",
    "vectors_updated": 8
  },
  "request_id": "..."
}
```

### Error codes
`QDRANT_CONNECTION_FAILED`, `QDRANT_UPSERT_FAILED`

---

# Endpoint Summary

## Shared Endpoints (no mode prefix)

| Method | Endpoint | Purpose | Auth |
|--------|----------|---------|------|
| GET | `/api/v1/health` | Liveness probe | None |
| GET | `/api/v1/ready` | Readiness probe | None / HMAC |
| GET | `/metrics` | Prometheus metrics | None |
| POST | `/api/v1/classify` | Classify + extract entities | HMAC |
| POST | `/api/v1/search` | Permission-aware semantic search | HMAC |
| POST | `/api/v1/collections/init` | Create Qdrant collection | HMAC |
| GET | `/api/v1/collections/stats` | Collection statistics | HMAC |

## Online Endpoints (`X-API-Key` required)

| Method | Endpoint | Purpose | Auth |
|--------|----------|---------|------|
| POST | `/api/v1/online/scrape` | Scrape webpage (Crawl4AI) | HMAC + API Key |
| POST | `/api/v1/online/crawl` | Discover URLs from site/sitemap | HMAC + API Key |
| POST | `/api/v1/online/parse` | Parse document from URL | HMAC + API Key |
| POST | `/api/v1/online/ingest` | Chunk + embed + store web content | HMAC + API Key |

## Local Endpoints (trusted network)

| Method | Endpoint | Purpose | Auth |
|--------|----------|---------|------|
| POST | `/api/v1/local/parse` | Parse document (SMB, R2) | HMAC |
| POST | `/api/v1/local/parse/upload` | Parse uploaded file | HMAC |
| POST | `/api/v1/local/discover` | Scan file sources for changes | HMAC |
| POST | `/api/v1/local/ingest` | Chunk + embed + store local docs | HMAC |
| DELETE | `/api/v1/local/vectors/{source_id}` | Delete document vectors | HMAC |
| POST | `/api/v1/local/vectors/delete-by-filter` | Delete vectors by metadata filter | HMAC |
| PUT | `/api/v1/local/vectors/update-acl` | Update ACL without re-embedding | HMAC |

---

# All Error Codes

| Category | Code | Description |
|----------|------|-------------|
| **Validation** | `VALIDATION_URL_INVALID` | URL is empty or doesn't start with http/https |
| | `VALIDATION_PATH_OUTSIDE_ROOTS` | Path not in allowed roots |
| | `VALIDATION_ACL_REQUIRED` | ACL missing from ingest request |
| | `VALIDATION_EMPTY_CONTENT` | Content/query is empty |
| | `VALIDATION_USER_REQUIRED` | User context missing from search |
| **Auth** | `AUTH_MISSING` | X-Signature or X-Timestamp header missing |
| | `AUTH_INVALID` | HMAC signature doesn't match |
| | `AUTH_EXPIRED` | Timestamp outside ±5 min window |
| | `AUTH_API_KEY_MISSING` | X-API-Key header missing on online endpoint |
| | `AUTH_API_KEY_INVALID` | X-API-Key not in `DP_ONLINE_API_KEYS` |
| **SMB** | `SMB_CONNECTION_FAILED` | Cannot connect to SMB share |
| | `SMB_AUTH_FAILED` | SMB credentials rejected |
| | `SMB_PATH_NOT_FOUND` | Share path doesn't exist |
| | `SMB_FILE_NOT_FOUND` | File not found on share |
| | `SMB_FILE_LOCKED` | File is locked by another process |
| **R2** | `R2_CONNECTION_FAILED` | Cannot connect to Cloudflare R2 |
| | `R2_FILE_NOT_FOUND` | Object key not found or presigned URL missing |
| | `R2_PRESIGNED_EXPIRED` | Pre-signed URL has expired |
| **LDAP** | `LDAP_CONNECTION_FAILED` | Cannot connect to LDAP/AD |
| | `LDAP_AUTH_FAILED` | LDAP bind credentials rejected |
| **Parse** | `PARSE_FAILED` | General parsing failure |
| | `PARSE_ENCRYPTED` | Document is password-protected |
| | `PARSE_CORRUPTED` | Document file is corrupted |
| | `PARSE_EMPTY` | Document has no extractable text |
| | `PARSE_TIMEOUT` | Parsing timed out |
| | `PARSE_UNSUPPORTED_FORMAT` | File format not supported |
| **Scrape** | `SCRAPE_FAILED` | General scraping failure |
| | `SCRAPE_BLOCKED` | Website blocked the request |
| | `SCRAPE_TIMEOUT` | Scraping timed out |
| | `SCRAPE_EMPTY` | Page returned no content |
| | `SCRAPE_ROBOTS_BLOCKED` | Blocked by robots.txt |
| **Crawl** | `CRAWL_SITEMAP_NOT_FOUND` | No URLs found in sitemap |
| | `CRAWL_MAX_URLS_EXCEEDED` | URL limit reached |
| **Classify** | `CLASSIFY_FAILED` | Classification failed |
| | `CLASSIFY_LOW_CONFIDENCE` | Confidence below threshold |
| | `ENTITY_EXTRACTION_FAILED` | Entity extraction failed |
| **Embedding** | `EMBEDDING_MODEL_NOT_LOADED` | BGE-M3 model not available |
| | `EMBEDDING_FAILED` | Embedding generation failed |
| | `EMBEDDING_OOM` | Out of memory during embedding |
| **Qdrant** | `QDRANT_CONNECTION_FAILED` | Cannot connect to Qdrant |
| | `QDRANT_COLLECTION_NOT_FOUND` | Collection doesn't exist |
| | `QDRANT_UPSERT_FAILED` | Failed to store vectors |
| | `QDRANT_SEARCH_FAILED` | Search query failed |
| | `QDRANT_DELETE_FAILED` | Failed to delete vectors |
| | `QDRANT_DISK_FULL` | Qdrant disk space exhausted |
