from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Data Plane internal settings."""

    model_config = {"env_prefix": "DP_"}

    # Auth
    hmac_secret: str = ""  # HMAC-SHA256 shared secret (empty = auth disabled)
    hmac_max_age: int = 300  # Max age of signed requests in seconds

    # CORS
    cors_origins: str = "*"

    # Scraping defaults
    default_timeout: int = 30
    max_concurrent: int = 10
    max_batch_urls: int = 50
    max_sitemap_pages: int = 500

    # Cache
    cache_ttl: int = 3600

    # Parsing
    max_file_size_mb: int = 50

    # Ingest
    default_chunk_size: int = 512
    default_chunk_overlap: int = 50

    # Search
    default_top_k: int = 10
    default_score_threshold: float = 0.5

    # Online API key security
    online_api_keys: str = ""  # Comma-separated valid API keys for online endpoints

    # Logging
    log_level: str = "info"
    log_json: bool = True

    # Deployment
    mode: str = "on-premise"  # "on-premise" or "cloud"
    tenant_id: str = ""
    worker_id: str = ""
    version: str = "1.0.0"


class ExternalSettings(BaseSettings):
    """Settings for external services — no env prefix."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # Crawl4AI
    crawl4ai_url: str = "http://crawl4ai:11235"
    crawl4ai_api_token: str = ""

    # Jina Reader (fallback scraper)
    jina_api_url: str = "https://eu-r-beta.jina.ai"
    jina_api_key: str = ""

    # LlamaParse (cloud document parsing)
    llama_cloud_api_key: str = ""  # empty = use local unstructured parser
    llama_cloud_base_url: str = "https://api.cloud.llamaindex.ai/api/v1/parsing"  # EU: https://api.cloud.eu.llamaindex.ai/api/v1/parsing

    # BGE-M3
    bge_m3_url: str = "http://bge-m3:8080"

    # Qdrant
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = ""  # Default collection name (tenant-based)

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Rate limiting
    rate_limit_per_domain: int = 10
    rate_limit_window: int = 60

    # ClickHouse
    clickhouse_required: bool = False
    clickhouse_host: str = "clickhouse"
    clickhouse_port: int = 9000
    clickhouse_db: str = "ki2_audit"
    clickhouse_user: str = "dataplane"
    clickhouse_password: str = ""

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # LDAP
    ldap_url: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_base_dn: str = ""

    # SMB
    smb_username: str = ""
    smb_password: str = ""
    smb_domain: str = ""

    # Cloudflare R2
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""


settings = Settings()
ext = ExternalSettings()
