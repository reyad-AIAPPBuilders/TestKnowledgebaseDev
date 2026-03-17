import inspect
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.utils.logger import get_logger

log = get_logger(__name__)

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
MAX_DEPTH = 4


class SitemapParser:
    """Parse XML/robots/HTML sitemap sources and emit normalized page URLs."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; KI2-Bot/1.0)",
                    "Accept": "application/xml,text/xml,text/html,text/plain,*/*",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def parse(
        self,
        sitemap_url: str,
        max_urls: int = 100,
        url_filter: str | None = None,
        on_url: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> list[str]:
        normalized = self._normalize_url(sitemap_url)
        if not normalized:
            return []

        urls: list[str] = []
        seen_urls: set[str] = set()
        seen_sources: set[str] = set()

        parsed = urlparse(normalized)
        if parsed.scheme and parsed.netloc and not parsed.path.endswith("/robots.txt"):
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            await self._parse_recursive(
                robots_url,
                urls=urls,
                seen_urls=seen_urls,
                seen_sources=seen_sources,
                max_urls=max_urls,
                depth=0,
                url_filter=url_filter,
                on_url=on_url,
                silent_errors=True,
            )

        await self._parse_recursive(
            normalized,
            urls=urls,
            seen_urls=seen_urls,
            seen_sources=seen_sources,
            max_urls=max_urls,
            depth=0,
            url_filter=url_filter,
            on_url=on_url,
            silent_errors=False,
        )
        return urls[:max_urls]

    async def _parse_recursive(
        self,
        source_url: str,
        *,
        urls: list[str],
        seen_urls: set[str],
        seen_sources: set[str],
        max_urls: int,
        depth: int,
        url_filter: str | None,
        on_url: Callable[[str], Awaitable[None] | None] | None,
        silent_errors: bool,
    ) -> None:
        if len(urls) >= max_urls or depth > MAX_DEPTH:
            return
        if source_url in seen_sources:
            return
        seen_sources.add(source_url)

        log.info("sitemap_parsing", url=source_url, depth=depth)

        try:
            client = await self._get_client()
            response = await client.get(source_url)
            response.raise_for_status()
            content = response.text
            content_type = (response.headers.get("content-type") or "").lower()
        except Exception as exc:
            if not silent_errors:
                log.error("sitemap_fetch_error", url=source_url, error=str(exc))
            return

        stripped = content.lstrip()
        is_xml = (
            "xml" in content_type
            or stripped.startswith("<?xml")
            or stripped.startswith("<urlset")
            or stripped.startswith("<sitemapindex")
        )
        if is_xml:
            parsed = await self._parse_xml(
                content,
                source_url=source_url,
                urls=urls,
                seen_urls=seen_urls,
                seen_sources=seen_sources,
                max_urls=max_urls,
                depth=depth,
                url_filter=url_filter,
                on_url=on_url,
            )
            if parsed:
                return

        if "sitemap:" in content.lower() and ("text/plain" in content_type or source_url.endswith("/robots.txt")):
            await self._parse_robots(
                content,
                source_url=source_url,
                urls=urls,
                seen_urls=seen_urls,
                seen_sources=seen_sources,
                max_urls=max_urls,
                depth=depth,
                url_filter=url_filter,
                on_url=on_url,
            )
            return

        await self._parse_html(
            content,
            source_url=source_url,
            urls=urls,
            seen_urls=seen_urls,
            seen_sources=seen_sources,
            max_urls=max_urls,
            depth=depth,
            url_filter=url_filter,
            on_url=on_url,
        )

    async def _parse_xml(
        self,
        content: str,
        *,
        source_url: str,
        urls: list[str],
        seen_urls: set[str],
        seen_sources: set[str],
        max_urls: int,
        depth: int,
        url_filter: str | None,
        on_url: Callable[[str], Awaitable[None] | None] | None,
    ) -> bool:
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return False

        tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

        if tag == "sitemapindex":
            for sitemap_el in root.findall("sm:sitemap/sm:loc", SITEMAP_NS):
                if len(urls) >= max_urls:
                    break
                child_url = self._normalize_url((sitemap_el.text or "").strip())
                if child_url:
                    await self._parse_recursive(
                        child_url,
                        urls=urls,
                        seen_urls=seen_urls,
                        seen_sources=seen_sources,
                        max_urls=max_urls,
                        depth=depth + 1,
                        url_filter=url_filter,
                        on_url=on_url,
                        silent_errors=False,
                    )
            return True

        if tag == "urlset":
            for url_el in root.findall("sm:url/sm:loc", SITEMAP_NS):
                if len(urls) >= max_urls:
                    break
                page_url = self._normalize_url((url_el.text or "").strip())
                if page_url:
                    await self._add_url(page_url, urls, seen_urls, max_urls, url_filter, on_url)
            return True

        found_loc = False
        for loc in root.iter("loc"):
            if len(urls) >= max_urls:
                break
            raw = (loc.text or "").strip()
            if not raw:
                continue
            found_loc = True
            page_url = self._normalize_url(raw)
            if not page_url:
                continue
            if self._looks_like_sitemap(page_url):
                await self._parse_recursive(
                    page_url,
                    urls=urls,
                    seen_urls=seen_urls,
                    seen_sources=seen_sources,
                    max_urls=max_urls,
                    depth=depth + 1,
                    url_filter=url_filter,
                    on_url=on_url,
                    silent_errors=False,
                )
            else:
                await self._add_url(page_url, urls, seen_urls, max_urls, url_filter, on_url)
        return found_loc

    async def _parse_robots(
        self,
        content: str,
        *,
        source_url: str,
        urls: list[str],
        seen_urls: set[str],
        seen_sources: set[str],
        max_urls: int,
        depth: int,
        url_filter: str | None,
        on_url: Callable[[str], Awaitable[None] | None] | None,
    ) -> None:
        for line in content.splitlines():
            if len(urls) >= max_urls:
                break
            line = line.strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip().lower() != "sitemap":
                continue
            sitemap_candidate = self._normalize_url(value.strip(), base=source_url)
            if not sitemap_candidate:
                continue
            await self._parse_recursive(
                sitemap_candidate,
                urls=urls,
                seen_urls=seen_urls,
                seen_sources=seen_sources,
                max_urls=max_urls,
                depth=depth + 1,
                url_filter=url_filter,
                on_url=on_url,
                silent_errors=True,
            )

    async def _parse_html(
        self,
        content: str,
        *,
        source_url: str,
        urls: list[str],
        seen_urls: set[str],
        seen_sources: set[str],
        max_urls: int,
        depth: int,
        url_filter: str | None,
        on_url: Callable[[str], Awaitable[None] | None] | None,
    ) -> None:
        soup = BeautifulSoup(content, "lxml")
        for anchor in soup.find_all("a", href=True):
            if len(urls) >= max_urls:
                break
            href = (anchor.get("href") or "").strip()
            if not href:
                continue
            if href.startswith("#") or href.lower().startswith(("mailto:", "tel:", "javascript:")):
                continue

            candidate = self._normalize_url(href, base=source_url)
            if not candidate:
                continue

            if self._looks_like_sitemap(candidate):
                await self._parse_recursive(
                    candidate,
                    urls=urls,
                    seen_urls=seen_urls,
                    seen_sources=seen_sources,
                    max_urls=max_urls,
                    depth=depth + 1,
                    url_filter=url_filter,
                    on_url=on_url,
                    silent_errors=True,
                )
            else:
                await self._add_url(candidate, urls, seen_urls, max_urls, url_filter, on_url)

    async def _add_url(
        self,
        url: str,
        urls: list[str],
        seen_urls: set[str],
        max_urls: int,
        url_filter: str | None,
        on_url: Callable[[str], Awaitable[None] | None] | None,
    ) -> None:
        if len(urls) >= max_urls:
            return
        if url in seen_urls:
            return
        if url_filter and url_filter.lower() not in url.lower():
            return
        seen_urls.add(url)
        urls.append(url)
        if on_url is None:
            return
        callback_result = on_url(url)
        if inspect.isawaitable(callback_result):
            await callback_result

    @staticmethod
    def _looks_like_sitemap(url: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path.lower()
        return path.endswith(".xml") or "sitemap" in path

    @staticmethod
    def _normalize_url(url: str, base: str | None = None) -> str:
        if not url:
            return ""
        candidate = urljoin(base or "", url).strip()
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return candidate
