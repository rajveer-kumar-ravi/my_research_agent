"""
Validation helpers for user input and URLs encountered during scraping.
"""
import re
from urllib.parse import urlparse

_BLOCKED_SCHEMES = {"file", "ftp", "javascript", "data"}
_BLOCKED_HOST_PATTERNS = [
    re.compile(r"^localhost$", re.IGNORECASE),
    re.compile(r"^127\."),
    re.compile(r"^0\.0\.0\.0$"),
    re.compile(r"^10\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^169\.254\."),  # link-local / cloud metadata
    re.compile(r"^::1$"),
]


def is_safe_url(url: str) -> bool:
    """
    Basic SSRF / malformed-URL guard: only allow http(s) URLs pointing at
    what appears to be a public hostname. This is a heuristic, not a
    complete SSRF defense (a full defense would resolve DNS and check the
    resulting IP), but it blocks the obvious local/internal targets.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False

    host = parsed.hostname or ""
    for pattern in _BLOCKED_HOST_PATTERNS:
        if pattern.match(host):
            return False

    return True


def is_valid_query(query: str) -> bool:
    return bool(query and len(query.strip()) >= 8)


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "..."
