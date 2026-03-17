from pydantic import BaseModel, Field


class ScrapeRequest(BaseModel):
    """Request to scrape a single webpage and extract its content as Markdown."""

    url: str = Field(..., description="Full URL of the webpage to scrape (must start with http:// or https://)")

    model_config = {
        "json_schema_extra": {
            "examples": [{"url": "https://www.wiener-neudorf.gv.at/foerderungen"}]
        }
    }


class ScrapeData(BaseModel):
    """Scraped webpage content and metadata."""

    url: str = Field(..., description="The URL that was scraped")
    title: str | None = Field(None, description="Page title from <title> tag")
    content: str = Field(..., description="Extracted page content as Markdown")
    content_length: int = Field(..., description="Length of the content string in characters")
    language: str | None = Field(None, description="Detected language (ISO 639-1 code, e.g. 'de')")
    links_found: int = Field(0, description="Number of links discovered on the page")
    last_modified: str | None = Field(None, description="Last-Modified header value if present")


class CrawlRequest(BaseModel):
    """Request to discover URLs from a website via sitemap parsing or BFS crawling."""

    url: str = Field(..., description="Base URL or sitemap URL to crawl")
    method: str = Field(..., description="Discovery method: 'sitemap' (parse XML sitemap) or 'crawl' (BFS link following)", pattern=r"^(sitemap|crawl)$")
    max_depth: int = Field(3, ge=1, le=5, description="Maximum link-following depth for crawl method")
    max_urls: int = Field(500, ge=1, le=5000, description="Maximum number of URLs to return")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"url": "https://www.wiener-neudorf.gv.at/sitemap.xml", "method": "sitemap", "max_urls": 500},
                {"url": "https://www.wiener-neudorf.gv.at", "method": "crawl", "max_depth": 3, "max_urls": 100},
            ]
        }
    }


class CrawlUrl(BaseModel):
    """A discovered URL with its type classification."""

    url: str = Field(..., description="Discovered URL")
    type: str = Field(..., description="URL type: 'page' (HTML) or 'document' (PDF, DOCX, etc.)")
    last_modified: str | None = Field(None, description="Last modified date from sitemap")


class CrawlData(BaseModel):
    """Result of URL discovery via sitemap or crawl."""

    base_url: str = Field(..., description="The URL that was crawled")
    method_used: str = Field(..., description="Method that was used: sitemap or crawl")
    urls: list[CrawlUrl] = Field(..., description="List of discovered URLs")
    total_urls: int = Field(..., description="Total number of URLs discovered")
