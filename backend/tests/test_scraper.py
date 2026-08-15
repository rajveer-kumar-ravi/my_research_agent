"""
Tests for app/services/scraper_service.py.

The network boundary (`ScraperService._fetch`) is mocked per-test so these
tests never make real HTTP requests, per the "mock external APIs" testing
rule. Everything above that boundary (safety validation, HTML cleaning,
chunking, metadata, error handling) runs for real.
"""
import httpx
import pytest

from app.services.scraper_service import ScraperService

SAMPLE_HTML = """
<html><head><title>Sample Research Page</title></head>
<body>
<nav>Home About</nav>
<script>track();</script>
<article>
<h1>Sample Research Page</h1>
<p>This paragraph contains genuinely useful research content about evaluation methods.</p>
<p>Here is a second paragraph with more detail about production considerations and tradeoffs.</p>
</article>
<footer>All rights reserved</footer>
</body></html>
"""


def make_scraper() -> ScraperService:
    return ScraperService(timeout_seconds=5)


def test_scrape_url_success():
    scraper = make_scraper()
    scraper._fetch = lambda url: SAMPLE_HTML

    result = scraper.scrape_url("https://example.com/article")

    assert result.success is True
    assert result.error is None
    assert len(result.chunks) >= 1
    chunk = result.chunks[0]
    assert chunk.source_url == "https://example.com/article"
    assert chunk.title == "Sample Research Page"
    assert chunk.source_domain == "example.com"
    assert "evaluation methods" in chunk.text


def test_scrape_url_rejects_unsafe_url_without_fetching():
    scraper = make_scraper()
    called = {"count": 0}

    def spy_fetch(url):
        called["count"] += 1
        return SAMPLE_HTML

    scraper._fetch = spy_fetch
    result = scraper.scrape_url("http://localhost/admin")

    assert result.success is False
    assert "safety validation" in result.error
    assert called["count"] == 0  # must never attempt to fetch an unsafe URL


def test_scrape_url_handles_http_status_error():
    scraper = make_scraper()

    def raise_http_error(url):
        request = httpx.Request("GET", url)
        response = httpx.Response(status_code=404, request=request)
        raise httpx.HTTPStatusError("Not Found", request=request, response=response)

    scraper._fetch = raise_http_error
    result = scraper.scrape_url("https://example.com/missing")

    assert result.success is False
    assert "404" in result.error


def test_scrape_url_handles_timeout():
    scraper = make_scraper()

    def raise_timeout(url):
        raise httpx.TimeoutException("timed out")

    scraper._fetch = raise_timeout
    result = scraper.scrape_url("https://example.com/slow")

    assert result.success is False
    assert "timed out" in result.error.lower()


def test_scrape_url_handles_network_error():
    scraper = make_scraper()

    def raise_network_error(url):
        raise httpx.RequestError("connection reset")

    scraper._fetch = raise_network_error
    result = scraper.scrape_url("https://example.com/flaky")

    assert result.success is False
    assert "network" in result.error.lower()


def test_scrape_url_rejects_empty_body():
    scraper = make_scraper()
    scraper._fetch = lambda url: ""

    result = scraper.scrape_url("https://example.com/empty")

    assert result.success is False
    assert "empty" in result.error.lower()


def test_scrape_url_rejects_too_little_content():
    scraper = make_scraper()
    scraper._fetch = lambda url: "<html><body><p>too short</p></body></html>"

    result = scraper.scrape_url("https://example.com/thin")

    assert result.success is False
    assert "too little" in result.error.lower()


def test_scrape_many_isolates_per_url_failures():
    scraper = make_scraper()

    def selective_fetch(url):
        if "good" in url:
            return SAMPLE_HTML
        raise httpx.RequestError("simulated failure")

    scraper._fetch = selective_fetch

    urls = [
        ("https://example.com/good-1", None),
        ("https://example.com/bad-1", None),
        ("https://example.com/good-2", None),
    ]
    results = scraper.scrape_many(urls)

    assert len(results) == 3
    outcomes = {r.url: r.success for r in results}
    assert outcomes["https://example.com/good-1"] is True
    assert outcomes["https://example.com/bad-1"] is False
    assert outcomes["https://example.com/good-2"] is True
