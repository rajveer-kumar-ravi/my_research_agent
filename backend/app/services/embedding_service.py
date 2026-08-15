"""
Embedding service.

Wraps Sentence-Transformers behind a clean interface. The model is loaded
lazily and cached as a process-wide singleton so repeated research runs
within the same server process don't reload the model from disk each time
(model loading is the single most expensive part of this service).
"""
from threading import Lock
from typing import List, Optional

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_model_lock = Lock()
_model_cache: dict = {}


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""


class EmbeddingService:
    def __init__(self, model_name: Optional[str] = None):
        self._model_name = model_name or get_settings().embedding_model

    def _get_model(self):
        """Thread-safe lazy singleton so the model is loaded from disk only once."""
        if self._model_name in _model_cache:
            return _model_cache[self._model_name]

        with _model_lock:
            if self._model_name not in _model_cache:
                logger.info("Loading embedding model %r ...", self._model_name)
                from sentence_transformers import SentenceTransformer

                _model_cache[self._model_name] = SentenceTransformer(self._model_name)
                logger.info("Embedding model %r loaded.", self._model_name)
        return _model_cache[self._model_name]

    def embed_texts(self, texts: List[str], batch_size: int = 32) -> np.ndarray:
        """Embed a batch of texts. Returns an (N, D) float32 numpy array."""
        if not texts:
            return np.zeros((0, self.dimension), dtype="float32")

        try:
            model = self._get_model()
            embeddings = model.encode(
                texts,
                batch_size=batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,  # cosine similarity via inner product
            )
            return embeddings.astype("float32")
        except Exception as exc:
            logger.error("Embedding generation failed: %s", exc)
            raise EmbeddingError(f"Failed to generate embeddings: {exc}") from exc

    def embed_query(self, query: str) -> np.ndarray:
        """Embed a single query string. Returns a (D,) float32 numpy array."""
        return self.embed_texts([query])[0]

    @property
    def dimension(self) -> int:
        model = self._get_model()
        return model.get_sentence_embedding_dimension()


_default_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Module-level accessor for the default embedding service instance."""
    global _default_service
    if _default_service is None:
        _default_service = EmbeddingService()
    return _default_service
