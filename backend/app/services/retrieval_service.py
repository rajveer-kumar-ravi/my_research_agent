"""
Semantic retrieval service.

Builds an in-memory FAISS index per research request from the scraped
chunks' embeddings, retrieves the top-K chunks for a query, and ranks them
using a combination of semantic similarity, source quality, and (when
available) recency. Every returned chunk still carries its full source
metadata — retrieval NEVER discards or anonymizes provenance, since that
would make citations impossible.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.embedding_service import EmbeddingService, get_embedding_service
from app.services.scraper_service import DocumentChunk
from app.services.source_quality_service import SourceQualityService

logger = get_logger(__name__)


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    source_url: str
    title: str
    source_domain: str
    published_date: Optional[str]
    semantic_score: float
    quality_boost: float
    final_score: float


class RetrievalService:
    def __init__(
        self,
        embedding_service: Optional[EmbeddingService] = None,
        quality_service: Optional[SourceQualityService] = None,
    ):
        self._embedder = embedding_service or get_embedding_service()
        self._quality = quality_service or SourceQualityService()
        self._top_k = get_settings().top_k_chunks

    def retrieve(
        self, query: str, chunks: List[DocumentChunk], top_k: Optional[int] = None
    ) -> List[RetrievedChunk]:
        """
        Embed all chunks, build a temporary FAISS index, and return the
        top-K chunks ranked by a blend of semantic similarity and source
        quality, each still carrying full source metadata.
        """
        if not chunks:
            return []

        top_k = top_k or self._top_k

        texts = [c.text for c in chunks]
        chunk_embeddings = self._embedder.embed_texts(texts)
        query_embedding = self._embedder.embed_query(query)

        index = self._build_index(chunk_embeddings)
        k = min(top_k, len(chunks))
        scores, indices = index.search(query_embedding.reshape(1, -1), k)

        results: List[RetrievedChunk] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            chunk = chunks[idx]
            quality = self._quality.assess(chunk.source_domain, chunk.published_date)
            recency_boost = self._recency_boost(chunk.published_date)
            final_score = float(score) + quality.score_boost + recency_boost

            results.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    source_url=chunk.source_url,
                    title=chunk.title,
                    source_domain=chunk.source_domain,
                    published_date=chunk.published_date,
                    semantic_score=float(score),
                    quality_boost=quality.score_boost,
                    final_score=final_score,
                )
            )

        results.sort(key=lambda r: r.final_score, reverse=True)
        logger.info("Retrieved %d/%d chunks for query=%r", len(results), len(chunks), query)
        return results

    def _build_index(self, embeddings: np.ndarray):
        import faiss

        dim = embeddings.shape[1]
        # Embeddings are L2-normalized (see EmbeddingService), so inner
        # product is equivalent to cosine similarity.
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings)
        return index

    @staticmethod
    def _recency_boost(published_date: Optional[str]) -> float:
        """Small boost for recently published sources; neutral if unknown."""
        if not published_date:
            return 0.0
        try:
            parsed = datetime.fromisoformat(published_date.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - parsed).days
            if age_days < 0:
                return 0.0
            if age_days <= 180:
                return 0.05
            if age_days <= 365 * 2:
                return 0.02
            return 0.0
        except (ValueError, TypeError):
            return 0.0
