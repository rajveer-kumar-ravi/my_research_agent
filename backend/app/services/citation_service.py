"""
Citation service.

Ensures every claim in the final report is grounded in an actual retrieved
chunk. This service NEVER invents a source: if Gemini names a source URL
that doesn't correspond to a chunk we actually retrieved, that citation is
dropped, not trusted, and logged as a discrepancy. If a claim has no
matching supporting chunk at all, it is marked unsupported rather than
silently attached to an unrelated source.
"""
from dataclasses import dataclass
from typing import Dict, List

from app.core.logging import get_logger
from app.schemas.research import Claim, Source, SourceQuality
from app.services.retrieval_service import RetrievedChunk
from app.services.source_quality_service import SourceQualityService

logger = get_logger(__name__)


@dataclass
class CitationCheckResult:
    claims: List[Claim]
    dropped_citation_count: int


class CitationService:
    def __init__(self, quality_service: SourceQualityService | None = None):
        self._quality = quality_service or SourceQualityService()

    def build_sources(self, retrieved_chunks: List[RetrievedChunk]) -> List[Source]:
        """
        Deduplicate retrieved chunks into one Source entry per URL, using the
        highest semantic score seen for that URL as its relevance score.
        """
        by_url: Dict[str, Source] = {}
        for chunk in retrieved_chunks:
            quality = self._quality.assess(chunk.source_domain, chunk.published_date)
            if chunk.source_url not in by_url:
                by_url[chunk.source_url] = Source(
                    source_url=chunk.source_url,
                    title=chunk.title,
                    source_domain=chunk.source_domain,
                    publication_date=chunk.published_date,
                    relevance_score=round(chunk.final_score, 4),
                    quality=quality.quality,
                )
            else:
                existing = by_url[chunk.source_url]
                if chunk.final_score > existing.relevance_score:
                    existing.relevance_score = round(chunk.final_score, 4)

        sources = list(by_url.values())
        sources.sort(key=lambda s: s.relevance_score, reverse=True)
        return sources

    def validate_claims(
        self, raw_claims: List[dict], retrieved_chunks: List[RetrievedChunk]
    ) -> CitationCheckResult:
        """
        Cross-check each claim's cited URLs against URLs we actually
        retrieved evidence from. Citations pointing to unknown URLs are
        dropped (never trusted at face value); claims left with zero valid
        citations are kept but flagged with an empty source list so the
        frontend can visibly mark them as unsupported.
        """
        known_urls = {c.source_url for c in retrieved_chunks}
        dropped = 0
        validated_claims: List[Claim] = []

        for raw in raw_claims:
            cited = raw.get("supporting_source_urls", []) or []
            valid_urls = [url for url in cited if url in known_urls]
            dropped += len(cited) - len(valid_urls)

            validated_claims.append(
                Claim(
                    text=raw.get("text", ""),
                    supporting_source_urls=valid_urls,
                    confidence=raw.get("confidence", 0.5) if valid_urls else 0.0,
                )
            )

        if dropped:
            logger.warning(
                "Dropped %d citation(s) pointing to URLs outside the retrieved evidence set.",
                dropped,
            )

        return CitationCheckResult(claims=validated_claims, dropped_citation_count=dropped)
