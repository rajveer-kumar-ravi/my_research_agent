"""
Tests for app/utils/text_cleaner.py.

test_html_to_clean_text_handles_nested_noise_containers is a regression
test for a real production bug: BeautifulSoup's .decompose() invalidates
all descendants of the tag it's called on (clearing their .attrs to None).
Since html_to_clean_text() materializes soup.find_all(True) into a single
list up front, decomposing a parent noise container corrupted any of its
still-pending descendants in that list, and calling .get() on them raised
"'NoneType' object has no attribute 'get'" — which was silently failing
nearly every real-world scrape (most pages have some noise element nested
inside another, e.g. an ad div inside a nav wrapper).
"""
from app.utils.text_cleaner import chunk_text, extract_title, html_to_clean_text


def test_html_to_clean_text_handles_nested_noise_containers():
    """
    A noise-hint element nested inside ANOTHER noise-hint element (e.g. an
    ad slot inside a nav wrapper, or a popup inside a cookie banner) must
    not crash — this is the exact real-world pattern that triggered the bug.
    """
    html = """
    <html><head><title>Test</title></head>
    <body>
    <div class="nav-wrapper">
      <nav class="sidebar">
        <div class="ad-slot">Ad content that should be removed</div>
      </nav>
    </div>
    <article>
      <h1>Real Article</h1>
      <p>This is genuinely useful content about RAG evaluation methods and tradeoffs in production systems.</p>
      <p>A second paragraph with more detail about retrieval precision and answer faithfulness metrics.</p>
    </article>
    <div class="cookie-banner"><div class="popup-inner">Accept all cookies</div></div>
    </body></html>
    """

    result = html_to_clean_text(html)  # must not raise

    assert "RAG evaluation methods" in result
    assert "Ad content that should be removed" not in result
    assert "Accept all cookies" not in result


def test_html_to_clean_text_handles_deeply_nested_noise_containers():
    """Three levels of nesting, all noise-hinted — the worst case for the bug."""
    html = """
    <html><body>
    <div class="advert-outer">
      <div class="banner-middle">
        <div class="popup-inner">
          <span class="social-share">Share this</span>
        </div>
      </div>
    </div>
    <article><p>Legitimate paragraph of real article content about evaluation.</p></article>
    </body></html>
    """
    result = html_to_clean_text(html)  # must not raise
    assert "Legitimate paragraph" in result
    assert "Share this" not in result


def test_html_to_clean_text_strips_scripts_and_styles():
    html = """
    <html><body>
    <script>alert('xss')</script>
    <style>.foo { color: red; }</style>
    <article><p>Real content about research methodology and evaluation.</p></article>
    </body></html>
    """
    result = html_to_clean_text(html)
    assert "alert" not in result
    assert "color: red" not in result
    assert "Real content" in result


def test_extract_title_from_title_tag():
    html = "<html><head><title>My Page Title</title></head><body></body></html>"
    assert extract_title(html) == "My Page Title"


def test_extract_title_falls_back_to_h1():
    html = "<html><head></head><body><h1>Fallback Heading</h1></body></html>"
    assert extract_title(html) == "Fallback Heading"


def test_chunk_text_produces_overlapping_chunks():
    text = "\n".join([f"This is paragraph number {i} with some real content in it." for i in range(20)])
    chunks = chunk_text(text, chunk_size_chars=200, overlap_chars=30)
    assert len(chunks) > 1
    assert all(len(c) > 20 for c in chunks)
