"""
Shared pytest fixtures.

We stub out the heavy/network-bound third-party SDKs (sentence_transformers,
faiss, tavily, google.genai) at the module level BEFORE any app code
imports them, so the entire test suite runs fully offline and fast. Real
app logic (text cleaning, chunking, ranking, citation validation, graph
routing) runs unmocked — only the external network/GPU boundary is faked.
"""
import sys
import types

import numpy as np
import pytest

import os

# Pytest run hote waqt agar REDIS_URL set nahi hai ya 'redis' host par hai, 
# toh use automatically 'localhost' par map kar do taaki local pytest fail na ho.
if "REDIS_URL" not in os.environ or "redis:" in os.environ.get("REDIS_URL", ""):
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"


def _install_fake_sentence_transformers():
    module = types.ModuleType("sentence_transformers")

    class FakeSentenceTransformer:
        def __init__(self, name):
            self.name = name

        def encode(self, texts, **kwargs):
            # Deterministic pseudo-embeddings derived from text length/hash
            # so semantically similar-looking test strings score consistently.
            rng = np.random.RandomState(abs(hash(tuple(texts))) % (2**31))
            return rng.rand(len(texts), 8).astype("float32")

        def get_sentence_embedding_dimension(self):
            return 8

    module.SentenceTransformer = FakeSentenceTransformer
    sys.modules["sentence_transformers"] = module


def _install_fake_faiss():
    module = types.ModuleType("faiss")

    class FakeIndexFlatIP:
        def __init__(self, dim):
            self.dim = dim
            self.vecs = None

        def add(self, embeddings):
            self.vecs = embeddings

        def search(self, query, k):
            n = self.vecs.shape[0] if self.vecs is not None else 0
            k = min(k, n)
            if k == 0:
                return np.zeros((1, 0), dtype="float32"), np.zeros((1, 0), dtype="int64")
            scores = np.linspace(1.0, 0.3, k, dtype="float32").reshape(1, k)
            idx = np.arange(k).reshape(1, k)
            return scores, idx

    module.IndexFlatIP = FakeIndexFlatIP
    sys.modules["faiss"] = module


def _install_fake_tavily():
    module = types.ModuleType("tavily")

    class FakeTavilyClient:
        def __init__(self, api_key=None):
            self.api_key = api_key

        def search(self, **kwargs):
            return {"results": []}

    module.TavilyClient = FakeTavilyClient
    sys.modules["tavily"] = module


def _install_fake_google_genai():
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    types_mod = types.ModuleType("google.genai.types")
    errors_mod = types.ModuleType("google.genai.errors")

    class FakeClient:
        def __init__(self, api_key=None):
            self.api_key = api_key

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs):
            pass

    class FakeHttpOptions:
        def __init__(self, **kwargs):
            pass

    class FakeAPIError(Exception):
        code = 500

    genai_mod.Client = FakeClient
    types_mod.GenerateContentConfig = FakeGenerateContentConfig
    types_mod.HttpOptions = FakeHttpOptions
    errors_mod.APIError = FakeAPIError
    google_mod.genai = genai_mod

    sys.modules["google"] = google_mod
    sys.modules["google.genai"] = genai_mod
    sys.modules["google.genai.types"] = types_mod
    sys.modules["google.genai.errors"] = errors_mod


_install_fake_sentence_transformers()
_install_fake_faiss()
_install_fake_tavily()
_install_fake_google_genai()


@pytest.fixture(autouse=True)
def _reset_embedding_singleton():
    """Ensure each test gets a clean embedding-model cache."""
    from app.services import embedding_service

    embedding_service._model_cache.clear()
    yield
    embedding_service._model_cache.clear()
