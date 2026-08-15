"""
Centralized application configuration.

All environment-driven settings live here so the rest of the codebase
never touches `os.environ` directly. This keeps secrets out of business
logic and gives us a single, typed source of truth.
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that mean "the user copied .env.example but never filled this in" —
# these must NOT be treated as valid configured secrets anywhere in the app.
_PLACEHOLDER_VALUES = {
    "",
    "your_gemini_api_key_here",
    "your_tavily_api_key_here",
    "your_sentry_dsn_here",
}


def is_real_secret(value: str) -> bool:
    """
    True only if `value` looks like an actual configured secret, not an
    empty string or one of the literal placeholder values shipped in
    .env.example. Used everywhere a service decides whether it has a real
    API key, so a forgotten placeholder always fails fast with a clear
    message instead of being passed to a real SDK client.
    """
    return bool(value) and value.strip() not in _PLACEHOLDER_VALUES


class Settings(BaseSettings):
    # --- Gemini ---
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # --- Search provider ---
    search_api_key: str = ""

    # --- Sentry ---
    sentry_dsn: str = ""

    # --- App ---
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # --- CORS ---
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    # --- Database & Cache ---
    database_url: str = "sqlite:///./research_agent.db"
    redis_url: str = "redis://localhost:6379/0"  # Added Redis URL here

    # --- Pipeline tuning ---
    max_sub_questions: int = 4
    max_search_results_per_query: int = 5
    max_urls_to_scrape: int = 12
    chunk_size_chars: int = 1000
    chunk_overlap_chars: int = 150
    top_k_chunks: int = 10
    embedding_model: str = "all-MiniLM-L6-v2"
    scrape_timeout_seconds: int = 10
    http_max_retries: int = 2

    # --- Authentication ---
    # Single source of truth for session lifetime — never hardcode "30"
    # anywhere else in the codebase.
    auth_session_days: int = 30
    session_cookie_name: str = "session_token"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_gemini_configured(self) -> bool:
        return is_real_secret(self.gemini_api_key)

    @property
    def is_search_configured(self) -> bool:
        return is_real_secret(self.search_api_key)

    @property
    def is_google_oauth_configured(self) -> bool:
        return is_real_secret(self.google_client_id) and is_real_secret(self.google_client_secret)

    @property
    def cookie_secure(self) -> bool:
        # Only require HTTPS-only cookies outside local development, so the
        # app still works over plain http://localhost during dev.
        return self.app_env.lower() != "development"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton — avoids re-parsing env on every import."""
    return Settings()