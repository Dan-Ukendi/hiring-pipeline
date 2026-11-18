import re
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from jobpipe.sources.ats.common import clean
from jobpipe.text import html_to_text

_DESCRIPTION_SELECTORS = (
    '[itemprop="description"]',
    "#job-description",
    "#jobDescription",
    ".job-description",
    '[class*="job-description"]',
    '[class*="jobDescription"]',
    '[class*="job_description"]',
    '[class*="posting-description"]',
    '[class*="description"]',
    "article",
    "main",
)
_LOCATION_SELECTORS = (
    '[itemprop="jobLocation"]',
    '[itemprop="addressLocality"]',
    '[class*="job-location"]',
    '[class*="jobLocation"]',
    '[class*="location"]',
    "[data-location]",
)
_APPLY_TEXT = re.compile(r"\b(apply|postuler|solliciteer|solliciteren|bewerben|candidater)\b", re.IGNORECASE)
_LOCATION_LABEL = re.compile(r"^(?:location|lieu|locatie|plaats|standort|ort)\s*:\s*(.+)$", re.IGNORECASE)
_MIN_DESCRIPTION_CHARS = 200
_MAX_LOCATION_CHARS = 120


def _meta(tree: LexborHTMLParser, key: str) -> str | None:
    node = tree.css_first(f'meta[property="{key}"]') or tree.css_first(f'meta[name="{key}"]')
    return clean(node.attributes.get("content")) if node is not None else None


def _inner(node: LexborNode) -> str:
    return "".join(child.html or "" for child in node.iter(include_text=True)).strip()


def _description(tree: LexborHTMLParser) -> str | None:
    for selector in _DESCRIPTION_SELECTORS:
        for node in tree.css(selector):
            markup = _inner(node)
            if len(html_to_text(markup)) >= _MIN_DESCRIPTION_CHARS:
                return markup
    return None


def _location(tree: LexborHTMLParser) -> str | None:
    for selector in _LOCATION_SELECTORS:
        for node in tree.css(selector):
            text = clean(node.attributes.get("data-location")) or clean(" ".join(node.text(strip=True).split()))
            if text and len(text) <= _MAX_LOCATION_CHARS:
                return text
    return _labelled_location(tree)


def _apply_url(tree: LexborHTMLParser, page_url: str) -> str | None:
    for link in tree.css("a[href]"):
        href = (link.attributes.get("href") or "").strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")) or not href:
            continue
        if _APPLY_TEXT.search(link.text(strip=True) or ""):
            return urljoin(page_url, href)
    return None


def _labelled_location(tree: LexborHTMLParser) -> str | None:
    for node in tree.css("li, dt, tr, p"):
        text = " ".join(node.text(strip=True).split())
        match = _LOCATION_LABEL.match(text)
        if match and len(text) <= _MAX_LOCATION_CHARS:
            return clean(match.group(1))
    return None


def to_fields(html: str, page_url: str) -> dict[str, Any]:
    tree = LexborHTMLParser(html)
    for node in tree.css("script,style,noscript"):
        node.decompose()
    heading = tree.css_first("h1")
    canonical = tree.css_first('link[rel="canonical"]')
    description = _description(tree)
    title = clean(heading.text(strip=True)) if heading is not None else None
    return {
        "title": title or _meta(tree, "og:title"),
        "url": urljoin(page_url, canonical.attributes.get("href") or "") if canonical is not None else page_url,
        "company_name": _meta(tree, "og:site_name"),
        "location_raw": _location(tree),
        "apply_url": _apply_url(tree, page_url),
        "description_raw": description,
        "description_text": (html_to_text(description) or None) if description else None,
    }
