"""
Web content extraction service.

Fetches each candidate URL defensively (SSRF-guarded, HTTPS/HTTP only,
timeout-bound, redirect-limited, HTTP-error-handled), strips it down to
clean readable text, and splits it into metadata-carrying chunks ready for
embedding.

Security note: page content is treated as UNTRUSTED DATA throughout this
pipeline. This service never executes anything found on a page, and the
text it extracts is later wrapped in clear delimiters before being shown
to Gemini (see gemini_service.py) so that any instructions embedded in
scraped content cannot be mistaken for instructions from the user or the
system.
"""
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.utils.text_cleaner import chunk_text, extract_title, html_to_clean_text, new_chunk_id
from app.utils.validators import is_safe_url

logger = get_logger(__name__)

_MAX_CONTENT_BYTES = 3_000_000  # 3MB cap per page to bound memory/time
_USER_AGENT = "Mozilla/5.0 (compatible; ResearchAgentBot/1.0; +https://example.com/bot)"


@dataclass
class DocumentChunk:
    chunk_id: str
    text: str
    source_url: str
    title: str
    source_domain: str
    published_date: Optional[str] = None


@dataclass
class ScrapeResult:
    url: str
    success: bool
    chunks: List["DocumentChunk"] = field(default_factory=list)
    error: Optional[str] = None


class ScraperService:
    def __init__(self, timeout_seconds: Optional[int] = None):
        settings = get_settings()
        self._timeout = timeout_seconds or settings.scrape_timeout_seconds
        self._chunk_size = settings.chunk_size_chars
        self._chunk_overlap = settings.chunk_overlap_chars

    def scrape_url(self, url: str, published_date: Optional[str] = None) -> ScrapeResult:
        """Fetch and clean a single URL. Never raises — failures are captured in ScrapeResult."""
        if not is_safe_url(url):
            return ScrapeResult(url=url, success=False, error="URL failed safety validation.")

        try:
            html = self._fetch(url)
        except httpx.TimeoutException:
            logger.warning("Timeout scraping %s", url)
            return ScrapeResult(url=url, success=False, error="Request timed out.")
        except httpx.HTTPStatusError as exc:
            logger.warning("HTTP error scraping %s: %s", url, exc.response.status_code)
            return ScrapeResult(
                url=url, success=False, error=f"HTTP {exc.response.status_code} error."
            )
        except httpx.RequestError as exc:
            logger.warning("Request error scraping %s: %s", url, exc)
            return ScrapeResult(url=url, success=False, error="Network request failed.")
        except Exception as exc:  # defensive catch-all; scraping must never crash the pipeline
            logger.error("Unexpected scraping error for %s: %s", url, exc)
            return ScrapeResult(url=url, success=False, error="Unexpected error during scraping.")

        if not html or not html.strip():
            return ScrapeResult(url=url, success=False, error="Empty response body.")

        try:
            title = extract_title(html)
            clean_text = html_to_clean_text(html)
        except Exception as exc:
            logger.error("Failed to parse HTML for %s: %s", url, exc)
            return ScrapeResult(url=url, success=False, error="Failed to parse page content.")

        if len(clean_text) < 100:
            return ScrapeResult(
                url=url, success=False, error="Page yielded too little readable text."
            )

        domain = urlparse(url).netloc
        text_chunks = chunk_text(
            clean_text, chunk_size_chars=self._chunk_size, overlap_chars=self._chunk_overlap
        )

        chunks = [
            DocumentChunk(
                chunk_id=new_chunk_id(),
                text=chunk,
                source_url=url,
                title=title,
                source_domain=domain,
                published_date=published_date,
            )
            for chunk in text_chunks
        ]

        return ScrapeResult(url=url, success=True, chunks=chunks)

    def scrape_many(self, urls_with_dates: List[tuple[str, Optional[str]]]) -> List[ScrapeResult]:
        """Scrape multiple URLs sequentially, isolating failures per URL."""
        results = []
        for url, published_date in urls_with_dates:
            results.append(self.scrape_url(url, published_date=published_date))
        return results

    def _fetch(self, url: str) -> str:
        headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
        with httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
            max_redirects=3,
            headers=headers,
        ) as client:
            response = client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                raise httpx.RequestError(f"Unsupported content-type: {content_type}")

            # Re-validate the final URL after redirects (SSRF guard against redirect chains).
            final_url = str(response.url)
            if not is_safe_url(final_url):
                raise httpx.RequestError("Redirected to an unsafe URL.")

            content = response.content[:_MAX_CONTENT_BYTES]
            return content.decode(response.encoding or "utf-8", errors="ignore")
