"""
Tests for app/services/retrieval_service.py.

sentence_transformers and faiss are stubbed via tests/conftest.py, so
embedding math itself is fake — these tests verify the *logic* around
retrieval (metadata is never dropped, top_k is respected, quality/recency
scoring is actually applied), not real semantic similarity.
"""
from app.services.embedding_service import EmbeddingService
from app.services.retrieval_service import RetrievalService
from app.services.scraper_service import DocumentChunk


def make_chunk(url: str, domain: str, text: str = "Some evidence text about the research topic.") -> DocumentChunk:
    return DocumentChunk(
        chunk_id=f"chunk-{url}", text=text, source_url=url, title="Test Title", source_domain=domain
    )


def make_service() -> RetrievalService:
    return RetrievalService(embedding_service=EmbeddingService(model_name="fake-test-model"))


def test_retrieve_with_no_chunks_returns_empty_list():
    svc = make_service()
    assert svc.retrieve("any query", []) == []


def test_retrieve_preserves_full_source_metadata():
    chunks = [
        make_chunk("https://arxiv.org/abs/1", "arxiv.org"),
        make_chunk("https://blog.example.com/x", "blog.example.com"),
    ]
    svc = make_service()
    results = svc.retrieve("research query", chunks, top_k=2)

    assert len(results) == 2
    urls = {r.source_url for r in results}
    assert urls == {"https://arxiv.org/abs/1", "https://blog.example.com/x"}
    for r in results:
        assert r.title == "Test Title"
        assert r.chunk_id.startswith("chunk-")
        assert r.source_domain in ("arxiv.org", "blog.example.com")


def test_retrieve_respects_top_k_limit():
    chunks = [make_chunk(f"https://example.com/{i}", "example.com") for i in range(10)]
    svc = make_service()
    results = svc.retrieve("query", chunks, top_k=3)
    assert len(results) == 3


def test_retrieve_applies_source_quality_boost():
    """arxiv.org matches the HIGH-quality heuristic pattern and should get a +0.15 boost."""
    chunks = [make_chunk("https://arxiv.org/abs/42", "arxiv.org")]
    svc = make_service()
    results = svc.retrieve("query", chunks, top_k=1)

    assert len(results) == 1
    result = results[0]
    assert result.quality_boost == 0.15
    # No published_date supplied -> recency boost is 0, so the arithmetic
    # should be exactly semantic_score + quality_boost.
    assert abs(result.final_score - (result.semantic_score + result.quality_boost)) < 1e-6


def test_retrieve_unclassified_domain_gets_no_boost():
    chunks = [make_chunk("https://some-random-blog.example/post", "some-random-blog.example")]
    svc = make_service()
    results = svc.retrieve("query", chunks, top_k=1)

    assert results[0].quality_boost == 0.0
