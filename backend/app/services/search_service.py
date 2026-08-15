"""
Web search service.

Defines an abstract `SearchProvider` interface so the concrete search
backend (currently Tavily) can be swapped out later (Bing, SerpAPI, Google
CSE, ...) without touching any calling code. Only `TavilySearchProvider`
knows about the Tavily SDK; everything else depends on the abstraction.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    score: float = 0.0
    published_date: Optional[str] = None


class SearchProviderError(Exception):
    """Raised when a search provider fails to complete a search."""


class SearchProvider(ABC):
    """Abstract interface every search backend must implement."""

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        """Execute a web search and return normalized results."""
        raise NotImplementedError


class TavilySearchProvider(SearchProvider):
    """Search provider backed by the Tavily API (tavily-python SDK)."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or get_settings().search_api_key
        self._client = None  # lazily constructed to avoid network/import cost at import time

    def _get_client(self):
        if self._client is None:
            from app.core.config import is_real_secret

            if not is_real_secret(self._api_key):
                raise SearchProviderError(
                    "SEARCH_API_KEY is not configured. Set it in your .env file. "
                    "Get a free key at https://tavily.com"
                )
            from tavily import TavilyClient  # local import: keep import-time side effects minimal

            self._client = TavilyClient(api_key=self._api_key)
        return self._client

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        client = self._get_client()
        try:
            response = client.search(
                query=query,
                max_results=max_results,
                search_depth="advanced",
                include_answer=False,
                include_raw_content=False,
            )
        except Exception as exc:  # Tavily raises several distinct exception types
            logger.error("Tavily search failed for query=%r: %s", query, exc)
            raise SearchProviderError(f"Search provider request failed: {exc}") from exc

        results: List[SearchResult] = []
        for item in response.get("results", []):
            results.append(
                SearchResult(
                    url=item.get("url", ""),
                    title=item.get("title", "Untitled"),
                    snippet=item.get("content", ""),
                    score=float(item.get("score", 0.0) or 0.0),
                    published_date=item.get("published_date"),
                )
            )
        logger.info("Search query=%r returned %d results", query, len(results))
        return results


class NullSearchProvider(SearchProvider):
    """
    A no-op provider used when no search API key is configured, so the app
    can still boot and return a clear, actionable error instead of crashing.
    """

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        raise SearchProviderError(
            "No search provider is configured. Set SEARCH_API_KEY in your .env file."
        )


def get_search_provider() -> SearchProvider:
    """Factory returning the configured search provider."""
    settings = get_settings()
    if settings.is_search_configured:
        return TavilySearchProvider(api_key=settings.search_api_key)
    logger.warning("SEARCH_API_KEY not configured — using NullSearchProvider.")
    return NullSearchProvider()
