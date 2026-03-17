from pydantic import BaseModel, Field


class ServiceStatus(BaseModel):
    """Status of each external dependency checked during readiness."""

    qdrant: bool = Field(False, description="Qdrant vector database reachable")
    bge_m3: bool = Field(False, description="BGE-M3 embedding model loaded and responding")
    parser: bool = Field(False, description="Document parser available (LlamaParse or Unstructured)")
    crawl4ai: bool = Field(False, description="Crawl4AI web scraping service available")
    ldap: bool = Field(False, description="LDAP/Active Directory server reachable")
    redis: bool = Field(False, description="Redis cache reachable")


class HealthResponse(BaseModel):
    """Liveness check response. Returns immediately if the process is running."""

    status: str = Field("ok", description="Always 'ok' if the service is alive")
    uptime_seconds: float | None = Field(None, description="Seconds since service started")

    model_config = {
        "json_schema_extra": {
            "examples": [{"status": "ok", "uptime_seconds": 3421.5}]
        }
    }


class ReadyResponse(BaseModel):
    """Readiness check response.

    Without HMAC auth headers: returns minimal `{ready: true}`.
    With HMAC auth headers (X-Signature): returns full dependency status.
    """

    ready: bool = Field(..., description="True if all core services are operational")
    services: ServiceStatus | None = Field(None, description="Per-service health (only with HMAC auth)")
    mode: str | None = Field(None, description="Deployment mode: on-premise or cloud")
    tenant_id: str | None = Field(None, description="Municipality/tenant identifier")
    worker_id: str | None = Field(None, description="Worker instance identifier")
    version: str | None = Field(None, description="Data Plane version")
    uptime_seconds: float | None = Field(None, description="Seconds since service started")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "ready": True,
                    "services": {
                        "qdrant": True,
                        "bge_m3": True,
                        "parser": True,
                        "crawl4ai": True,
                        "ldap": True,
                        "redis": True,
                    },
                    "mode": "on-premise",
                    "tenant_id": "wiener-neudorf",
                    "worker_id": "wn-worker-01",
                    "version": "1.0.0",
                    "uptime_seconds": 3421.5,
                }
            ]
        }
    }
