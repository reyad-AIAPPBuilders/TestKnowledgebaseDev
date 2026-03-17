import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

# Elements to strip from HTML before markdown conversion
NOISE_SELECTORS = [
    "nav",
    "header",
    "footer",
    ".navbar",
    ".nav",
    ".navigation",
    ".menu",
    ".sidebar",
    ".cookie-banner",
    ".cookie-consent",
    ".cookie-notice",
    "#cookie-banner",
    "#cookie-consent",
    ".gdpr",
    ".ad",
    ".ads",
    ".advertisement",
    ".banner",
    ".popup",
    ".modal",
    ".overlay",
    "[role='banner']",
    "[role='navigation']",
    "[role='contentinfo']",
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    ".social-share",
    ".breadcrumb",
    ".pagination",
    ".print-only",
    ".skip-link",
]


def clean_html(html: str, css_selector: str | None = None) -> str:
    """Clean HTML by removing noise elements, keeping main content."""
    soup = BeautifulSoup(html, "lxml")

    if css_selector:
        selected = soup.select_one(css_selector)
        if selected:
            soup = BeautifulSoup(str(selected), "lxml")

    for selector in NOISE_SELECTORS:
        for el in soup.select(selector):
            el.decompose()

    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find(id=re.compile(r"(content|main|article)", re.I))
        or soup.find(class_=re.compile(r"(content|main|article)", re.I))
    )

    if main_content and isinstance(main_content, Tag):
        return str(main_content)

    body = soup.find("body")
    if body:
        return str(body)
    return str(soup)


def clean_markdown(markdown: str | None) -> str:
    """Post-process markdown for RAG pipeline quality."""
    if not markdown:
        return ""

    text = markdown
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = re.sub(r"\n[ \t]+\n", "\n\n", text)
    text = re.sub(r"\[([^\]]*)\]\(javascript:[^)]*\)", r"\1", text)
    text = re.sub(r"\[\s*\]\([^)]*\)", "", text)
    text = re.sub(r"!\[[^\]]*\]\(data:[^)]+\)", "", text)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"[\u00a0\u2000-\u200a\u202f\u205f]", " ", text)
    text = re.sub(r"(?<=\S) {2,}", " ", text)
    return text.strip()


def extract_metadata(html: str) -> dict[str, str | None]:
    """Extract page metadata from HTML head."""
    soup = BeautifulSoup(html, "lxml")

    title = None
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)

    description = None
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and isinstance(meta_desc, Tag):
        description = meta_desc.get("content", None)
        if isinstance(description, list):
            description = description[0] if description else None

    language = None
    html_tag = soup.find("html")
    if html_tag and isinstance(html_tag, Tag):
        language = html_tag.get("lang", None)
        if isinstance(language, list):
            language = language[0] if language else None

    return {"title": title, "description": description, "language": language}


def count_words(text: str | None) -> int:
    """Count words in text, handling German compound words properly."""
    if not text:
        return 0
    words = re.findall(r"\b\w+\b", text, re.UNICODE)
    return len(words)


def extract_links(html: str, base_url: str = "") -> list[str]:
    """Extract all href links from HTML and normalize against base_url."""
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if isinstance(href, list):
            href = href[0]
        href = href.strip()
        if href and not href.startswith(("javascript:", "mailto:", "tel:", "#")):
            links.append(urljoin(base_url, href) if base_url else href)
    return list(dict.fromkeys(links))


def extract_images(html: str) -> list[str]:
    """Extract all image source URLs from HTML."""
    soup = BeautifulSoup(html, "lxml")
    images: list[str] = []
    for img in soup.find_all("img", src=True):
        src = img["src"]
        if isinstance(src, list):
            src = src[0]
        src = src.strip()
        if src and not src.startswith("data:"):
            images.append(src)
    return list(dict.fromkeys(images))
