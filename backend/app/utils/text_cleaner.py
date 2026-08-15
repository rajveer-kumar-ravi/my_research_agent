"""
Text cleaning and chunking utilities used by the scraper and embedding
pipeline. Pure functions, no external dependencies beyond stdlib + bs4,
so they're trivial to unit test.
"""
import re
from typing import List
from uuid import uuid4

from bs4 import BeautifulSoup

# Tags whose content is never useful readable text.
_STRIP_TAGS = [
    "script", "style", "noscript", "nav", "footer", "header",
    "aside", "form", "iframe", "svg", "button", "input",
    "select", "option", "figure", "figcaption",
]

# Heuristic boilerplate phrases commonly found in ads/cookie banners/nav.
_BOILERPLATE_PATTERNS = [
    r"^\s*cookie(s)?\s+(policy|notice|consent)",
    r"^\s*subscribe to our newsletter",
    r"^\s*all rights reserved",
    r"^\s*share this (article|post)",
    r"^\s*advertisement\s*$",
    r"^\s*sign up for",
    r"^\s*accept all cookies",
]
_BOILERPLATE_RE = re.compile("|".join(_BOILERPLATE_PATTERNS), re.IGNORECASE)


def html_to_clean_text(html: str) -> str:
    """
    Strip an HTML document down to readable body text: removes scripts,
    styles, nav/ads/boilerplate elements, and collapses whitespace.
    """
    soup = BeautifulSoup(html, "lxml")

    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove elements that look like ad/nav containers by common class/id hints.
    # NOTE: soup.find_all(True) materializes a flat list of every tag up
    # front. Calling .decompose() on a tag also invalidates all of its
    # descendants (clearing their internal attrs to None) — since those
    # descendants may still appear later in this same list, we must skip
    # any tag that a previous iteration already decomposed, or `.get()`
    # below raises "'NoneType' object has no attribute 'get'".
    noise_hints = ("ad", "advert", "cookie", "banner", "popup", "nav", "sidebar", "social-share")
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        attr_blob = " ".join(
            [tag.get("class") and " ".join(tag.get("class")) or "", tag.get("id") or ""]
        ).lower()
        if any(hint in attr_blob for hint in noise_hints):
            tag.decompose()

    text = soup.get_text(separator="\n")
    return normalize_whitespace(remove_boilerplate_lines(text))


def remove_boilerplate_lines(text: str) -> str:
    lines = text.split("\n")
    kept = [line for line in lines if not _BOILERPLATE_RE.search(line.strip())]
    return "\n".join(kept)


def normalize_whitespace(text: str) -> str:
    # Collapse runs of blank lines and trailing/leading whitespace per line.
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    # Drop very short noise lines (menu items, single words) that add no value,
    # but keep lines that look like real sentences.
    cleaned_lines = [line for line in lines if len(line) > 3]
    return "\n".join(cleaned_lines)


def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    return "Untitled"


def chunk_text(
    text: str, chunk_size_chars: int = 1000, overlap_chars: int = 150
) -> List[str]:
    """
    Split text into overlapping chunks on paragraph boundaries where
    possible, falling back to hard character splits for very long
    paragraphs. Overlap preserves context across chunk boundaries for
    better retrieval quality.
    """
    if not text:
        return []

    paragraphs = [p for p in text.split("\n") if p.strip()]
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 1 <= chunk_size_chars:
            current = f"{current}\n{para}" if current else para
        else:
            if current:
                chunks.append(current.strip())
            if len(para) > chunk_size_chars:
                # Hard-split an overly long paragraph.
                for i in range(0, len(para), chunk_size_chars - overlap_chars):
                    chunks.append(para[i:i + chunk_size_chars].strip())
                current = ""
            else:
                current = para

    if current.strip():
        chunks.append(current.strip())

    # Apply overlap by prepending the tail of the previous chunk.
    if overlap_chars > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            tail = chunks[i - 1][-overlap_chars:]
            overlapped.append(f"{tail}\n{chunks[i]}")
        chunks = overlapped

    return [c for c in chunks if len(c.strip()) > 20]


def new_chunk_id() -> str:
    return str(uuid4())
