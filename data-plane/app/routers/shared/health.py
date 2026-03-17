"""
GET /api/v1/health — Liveness check (no auth)
GET /api/v1/ready  — Readiness check (minimal without auth, full with HMAC)
"""

import time

from fastapi import APIRouter, Request

from app.config import settings
from app.models.health import HealthResponse, ReadyResponse, ServiceStatus
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Health"])


def _uptime(request: Request) -> float:
    start = getattr(request.app.state, "start_time", None)
    if start is None:
        return 0.0
    return round(time.monotonic() - start, 1)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness check",
    description="Returns `{status: ok}` if the Data Plane process is running. No authentication required. Used by container orchestrators (Docker, Kubernetes) as a liveness probe.",
    response_description="Service is alive",
)
async def health(request: Request) -> HealthResponse:
    return HealthResponse(status="ok", uptime_seconds=_uptime(request))


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness check",
    description="""Check if the Data Plane and all its dependencies are ready to serve requests.

**Without HMAC auth headers:** Returns minimal `{ready: true/false}` — suitable for load balancer health checks.

**With HMAC auth headers (X-Signature + X-Timestamp):** Returns full dependency status including Qdrant, BGE-M3, Parser (LlamaParse/Unstructured), Crawl4AI, LDAP, and Redis.

Core services that must be healthy for `ready: true`: Qdrant, BGE-M3, Parser, Crawl4AI.""",
    response_description="Readiness status with optional service details",
)
async def ready(request: Request) -> ReadyResponse:
    has_auth = bool(request.headers.get("X-Signature"))
    uptime = _uptime(request)

    if not has_auth:
        return ReadyResponse(ready=True, uptime_seconds=uptime)

    services = ServiceStatus()

    # Crawl4AI
    scraping_svc = getattr(request.app.state, "scraping", None)
    if scraping_svc:
        services.crawl4ai = getattr(scraping_svc, "is_ready", False)

    # Qdrant
    qdrant = getattr(request.app.state, "qdrant", None)
    if qdrant:
        try:
            services.qdrant = await qdrant.check_health()
        except Exception:
            services.qdrant = False

    # BGE-M3
    embedder = getattr(request.app.state, "embedder", None)
    if embedder:
        try:
            services.bge_m3 = await embedder.check_health()
        except Exception:
            services.bge_m3 = False

    # Parser (LlamaParse or Unstructured)
    parser = getattr(request.app.state, "parser", None)
    if parser:
        try:
            services.parser = await parser.check_health()
        except Exception:
            services.parser = False

    # LDAP
    ldap = getattr(request.app.state, "ldap", None)
    if ldap:
        try:
            services.ldap = await ldap.check_health()
        except Exception:
            services.ldap = False

    # Redis (cache)
    cache = getattr(request.app.state, "cache", None)
    if cache:
        try:
            services.redis = await cache.ping()
        except Exception:
            services.redis = False

    all_ready = all([
        services.crawl4ai,
        services.qdrant,
        services.bge_m3,
        services.parser,
    ])

    return ReadyResponse(
        ready=all_ready,
        services=services,
        mode=settings.mode,
        tenant_id=settings.tenant_id,
        worker_id=settings.worker_id,
        version=settings.version,
        uptime_seconds=uptime,
    )
