import random
import time

import httpx
from bs4 import BeautifulSoup

from app.config import ext, settings
from app.services.metrics import mark_crawl4ai
from app.utils.content import clean_html, clean_markdown
from app.utils.logger import get_logger

log = get_logger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

ACCEPT_LANGUAGES = [
    "de-AT,de;q=0.9,en;q=0.8",
    "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "de,en-US;q=0.9,en;q=0.8",
]


class CrawlResult:
    def __init__(
        self,
        *,
        markdown: str = "",
        html: str = "",
        success: bool = True,
        error: str | None = None,
        duration_ms: int = 0,
    ):
        self.markdown = markdown
        self.html = html
        self.success = success
        self.error = error
        self.duration_ms = duration_ms


class Crawl4AIClient:
    """HTTP client wrapper for external Crawl4AI with Jina Reader and httpx fallback."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._base_url = ext.crawl4ai_url.rstrip("/")
        self._api_token = ext.crawl4ai_api_token
        self._jina_url = ext.jina_api_url.rstrip("/")
        self._jina_key = ext.jina_api_key

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.default_timeout + 10, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        log.info("crawl4ai_client_started", base_url=self._base_url, jina_fallback=bool(self._jina_key))

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def check_health(self) -> bool:
        if not self._client:
            return False
        try:
            resp = await self._client.get(f"{self._base_url}/health", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def crawl(
        self,
        url: str,
        *,
        js_render: bool = True,
        wait_for: str | None = None,
        css_selector: str | None = None,
        timeout: int | None = None,
    ) -> CrawlResult:
        if not self._client:
            raise RuntimeError("Client not started — call start() first")

        req_timeout = timeout or settings.default_timeout
        start = time.monotonic()

        if js_render:
            try:
                result = await self._crawl_via_api(
                    url,
                    wait_for=wait_for,
                    css_selector=css_selector,
                    timeout=req_timeout,
                )
                result.duration_ms = int((time.monotonic() - start) * 1000)
                if result.success:
                    mark_crawl4ai("success", time.monotonic() - start)
                    return result
                log.warning("crawl4ai_failed_falling_back", url=url, error=result.error)
            except Exception as exc:
                log.warning("crawl4ai_unavailable_falling_back", url=url, error=str(exc))

            mark_crawl4ai("failed", time.monotonic() - start)

        # Fallback 2: Jina Reader API
        if self._jina_key:
            try:
                result = await self._scrape_with_jina(url, timeout=req_timeout)
                result.duration_ms = int((time.monotonic() - start) * 1000)
                if result.success:
                    log.info("jina_fallback_success", url=url)
                    return result
                log.warning("jina_fallback_failed", url=url, error=result.error)
            except Exception as exc:
                log.warning("jina_fallback_error", url=url, error=str(exc))

        # Fallback 3: Raw httpx
        result = await self._scrape_with_httpx(url, css_selector=css_selector, timeout=req_timeout)
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    async def _crawl_via_api(
        self,
        url: str,
        *,
        wait_for: str | None = None,
        css_selector: str | None = None,
        timeout: int = 30,
    ) -> CrawlResult:
        headers: dict[str, str] = {}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"

        crawler_params: dict = {
            "scan_full_page": True,
            "wait_until": "networkidle",
            "page_timeout": timeout * 1000,
            "delay_before_return_html": 2.0,
            "magic": True,
            "remove_overlay_elements": True,
            "cache_mode": "bypass",
        }
        if wait_for:
            crawler_params["wait_for"] = f"css:{wait_for}"
        if css_selector:
            crawler_params["css_selector"] = css_selector

        payload: dict = {
            "urls": [url],
            "browser_config": {
                "type": "BrowserConfig",
                "params": {"headless": True},
            },
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": crawler_params,
            },
        }

        resp = await self._client.post(  # type: ignore[union-attr]
            f"{self._base_url}/crawl",
            json=payload,
            headers=headers,
            timeout=timeout + 10,
        )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results") or []
        if isinstance(results, list) and results:
            result_data = results[0]
        else:
            result_data = data.get("result", data)

        success = bool(result_data.get("success", data.get("success", False)))
        markdown = _extract_markdown(result_data.get("markdown"))
        html = _extract_html(result_data)
        error = _extract_error(result_data)

        return CrawlResult(markdown=clean_markdown(markdown), html=html, success=success, error=error)

    async def _scrape_with_jina(self, url: str, *, timeout: int = 30) -> CrawlResult:
        """Scrape a URL via Jina Reader API — returns Markdown directly."""
        headers = {
            "Authorization": f"Bearer {self._jina_key}",
            "Accept": "application/json",
            "X-Return-Format": "markdown",
        }

        try:
            resp = await self._client.get(  # type: ignore[union-attr]
                f"{self._jina_url}/{url}",
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            return CrawlResult(success=False, error=f"Jina timeout after {timeout}s")
        except httpx.HTTPStatusError as exc:
            return CrawlResult(success=False, error=f"Jina HTTP {exc.response.status_code}")
        except Exception as exc:
            return CrawlResult(success=False, error=f"Jina error: {exc}")

        data = resp.json()
        content = data.get("data", {}).get("content", "")
        title = data.get("data", {}).get("title", "")

        if not content.strip():
            return CrawlResult(success=False, error="Jina returned empty content")

        markdown = clean_markdown(content)
        return CrawlResult(markdown=markdown, html="", success=True)

    async def _scrape_with_httpx(self, url: str, *, css_selector: str | None = None, timeout: int = 30) -> CrawlResult:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": random.choice(ACCEPT_LANGUAGES),
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

        try:
            response = await self._client.get(url, headers=headers, timeout=timeout)  # type: ignore[union-attr]
            response.raise_for_status()
        except httpx.TimeoutException:
            return CrawlResult(success=False, error=f"Timeout after {timeout}s")
        except httpx.HTTPStatusError as exc:
            return CrawlResult(success=False, error=f"HTTP {exc.response.status_code}")
        except Exception as exc:
            return CrawlResult(success=False, error=str(exc))

        content_type = response.headers.get("content-type", "")
        if "charset" not in content_type.lower():
            response.encoding = "utf-8"
        raw_html = response.text

        cleaned = clean_html(raw_html, css_selector)
        soup = BeautifulSoup(cleaned, "lxml")
        markdown = clean_markdown(_html_to_markdown(soup))

        return CrawlResult(markdown=markdown, html=raw_html, success=True)


def _html_to_markdown(soup: BeautifulSoup) -> str:
    lines: list[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "blockquote", "pre"]):
        tag = el.name
        text = el.get_text(separator=" ", strip=True)
        if not text:
            continue
        if tag == "h1":
            lines.append(f"# {text}\n")
        elif tag == "h2":
            lines.append(f"## {text}\n")
        elif tag == "h3":
            lines.append(f"### {text}\n")
        elif tag == "h4":
            lines.append(f"#### {text}\n")
        elif tag in ("h5", "h6"):
            lines.append(f"##### {text}\n")
        elif tag == "li":
            lines.append(f"- {text}")
        elif tag == "blockquote":
            lines.append(f"> {text}\n")
        elif tag == "pre":
            lines.append(f"```\n{text}\n```\n")
        else:
            lines.append(f"{text}\n")
    return "\n".join(lines)


def _extract_markdown(markdown_value: object) -> str:
    if isinstance(markdown_value, str):
        return markdown_value
    if isinstance(markdown_value, dict):
        for key in ("raw_markdown", "markdown_with_citations", "fit_markdown"):
            value = markdown_value.get(key)
            if isinstance(value, str) and value:
                return value
        return ""
    return ""


def _extract_html(result_data: dict) -> str:
    for key in ("html", "cleaned_html", "fit_html"):
        value = result_data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_error(result_data: dict) -> str | None:
    for key in ("error_message", "error"):
        value = result_data.get(key)
        if isinstance(value, str) and value:
            return value
    return None
