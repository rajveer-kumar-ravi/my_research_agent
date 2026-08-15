"""
Source quality heuristic.

IMPORTANT: this is a transparent, rule-based HEURISTIC, not a certified
measure of authority or accuracy. It is intentionally simple and fully
visible in code (no hidden ML classifier) so its judgments can be audited
and disputed. It should be read as "this domain pattern is commonly
associated with X reliability", not as a factual claim about any specific
page's correctness.
"""
import re
from dataclasses import dataclass
from typing import Optional

from app.schemas.research import SourceQuality

# High: official docs, government, academic, well-known standards bodies.
_HIGH_QUALITY_PATTERNS = [
    r"\.gov$", r"\.gov\.", r"\.edu$", r"\.edu\.",
    r"\.ac\.[a-z]{2}$",
    r"arxiv\.org$", r"acm\.org$", r"ieee\.org$",
    r"nature\.com$", r"science\.org$", r"ncbi\.nlm\.nih\.gov$",
    r"docs\.python\.org$", r"developer\.mozilla\.org$",
    r"w3\.org$", r"iso\.org$",
]

# Medium: established technical publications / reputable news & tech sites.
_MEDIUM_QUALITY_DOMAINS = {
    "techcrunch.com", "wired.com", "arstechnica.com", "theverge.com",
    "reuters.com", "apnews.com", "bloomberg.com", "nytimes.com",
    "wsj.com", "bbc.com", "bbc.co.uk", "economist.com",
    "github.com", "stackoverflow.com", "medium.com",
    "oreilly.com", "infoq.com", "thenewstack.io",
    "huggingface.co", "openai.com", "anthropic.com", "google.com",
    "microsoft.com", "aws.amazon.com",
}

# Low: known low-signal aggregators / content farms.
_LOW_QUALITY_DOMAINS = {
    "pinterest.com", "quora.com", "answers.com",
}

_HIGH_RE = re.compile("|".join(_HIGH_QUALITY_PATTERNS), re.IGNORECASE)


@dataclass
class QualityAssessment:
    quality: SourceQuality
    score_boost: float  # additive boost applied during evidence ranking
    reason: str


class SourceQualityService:
    """Scores a source domain using transparent, inspectable heuristics."""

    def assess(self, domain: str, published_date: Optional[str] = None) -> QualityAssessment:
        domain = (domain or "").lower().strip()

        if _HIGH_RE.search(domain):
            return QualityAssessment(
                quality=SourceQuality.HIGH,
                score_boost=0.15,
                reason="Domain matches a known official/academic/government pattern.",
            )

        if domain in _MEDIUM_QUALITY_DOMAINS or any(
            domain.endswith("." + d) for d in _MEDIUM_QUALITY_DOMAINS
        ):
            return QualityAssessment(
                quality=SourceQuality.MEDIUM,
                score_boost=0.05,
                reason="Domain matches a known established technical/news publication.",
            )

        if domain in _LOW_QUALITY_DOMAINS:
            return QualityAssessment(
                quality=SourceQuality.LOW,
                score_boost=-0.10,
                reason="Domain matches a known low-signal aggregator pattern.",
            )

        # Default: unknown domain treated as medium-low with no boost. We do
        # NOT claim to know an unfamiliar domain is untrustworthy — absence
        # of a match means "unclassified", not "bad".
        return QualityAssessment(
            quality=SourceQuality.MEDIUM,
            score_boost=0.0,
            reason="Domain not in known pattern lists; treated as unclassified/medium.",
        )
