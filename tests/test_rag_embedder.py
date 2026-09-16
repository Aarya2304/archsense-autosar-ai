"""M2 tests: embedder providers (fast, deterministic only).

The hashing embedder is exercised for real; Sentence-Transformers models are
mocked (no downloads in the default test run -- real-model tests are marked
``model`` and opt-in).
"""

from __future__ import annotations

import math

import pytest

from backend.rag.embedder import (HashingEmbedder, estimate_model_size_mb,
                                  get_embedder, timed_embed)


def test_hashing_embedder_is_deterministic():
    e = HashingEmbedder()
    a = e.embed(["DoorStatusIF provides door state signals"])
    b = e.embed(["DoorStatusIF provides door state signals"])
    assert a == b


def test_hashing_embedder_dimensions_and_normalization():
    e = HashingEmbedder(dim=256)
    vecs = e.embed(["short", "a much longer text about AUTOSAR ports "
                    "and interfaces and signals"])
    assert len(vecs) == 2
    for v in vecs:
        assert len(v) == 256
        norm = math.sqrt(sum(x * x for x in v))
        assert norm == pytest.approx(1.0, abs=1e-6)


def test_hashing_embedder_similarity_ordering():
    """Same-topic strings must score closer than unrelated ones."""
    e = HashingEmbedder()
    q = e.embed_query(["Which component provides the VehicleSpeed signal?"])[0]
    near = e.embed(["VehicleSpeed is provided by SpeedProviderSWC "
                    "via VehicleSpeedIF"])[0]
    far = e.embed(["The NvM stores three seat memory profiles"])[0]
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))
    assert dot(q, near) > dot(q, far)


def test_embedder_protocol_satisfied():
    e = HashingEmbedder()
    assert isinstance(e.name, str) and e.name
    assert e.dimension == 512
    vectors, timing = timed_embed(e, ["a", "b", "c"])
    assert len(vectors) == 3
    assert timing.n_texts == 3
    assert timing.total_seconds >= 0.0
    assert timing.texts_per_second >= 0.0


def test_size_estimate_returns_none_for_hashing():
    assert estimate_model_size_mb(HashingEmbedder()) is None


def test_get_embedder_resolves_hashing():
    e = get_embedder("hashing")
    assert isinstance(e, HashingEmbedder)


def test_e5_prefix_convention_applies():
    """E5-family names must map to query:/passage: prefixes (no download)."""
    from backend.rag.embedder import _prefixes_for
    assert _prefixes_for("intfloat/e5-small-v2") == ("query: ", "passage: ")
    assert _prefixes_for("all-MiniLM-L6-v2") == ("", "")


class _FakeVec(list):
    """Vector stand-in mimicking numpy's ``.tolist()`` interface."""

    def tolist(self) -> list:
        return list(self)


class _FakeSTModel:
    """Minimal SentenceTransformer stand-in for retriever-level tests."""

    def __init__(self, dim: int = 8):
        self._dim = dim
        self.calls: list[list[str]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts, batch_size=32, normalize_embeddings=False,
               convert_to_numpy=True, show_progress_bar=False):
        self.calls.append(list(texts))
        return [_FakeVec([1.0 / math.sqrt(self._dim)] * self._dim)
                for _ in texts]


def test_st_embedder_applies_prefixes(monkeypatch):
    import sys
    import types

    fake_st = types.ModuleType("sentence_transformers")

    class FakeSentenceTransformer(_FakeSTModel):
        def __init__(self, model_name, **kwargs):
            self.model_name = model_name
            self.kwargs = kwargs
            _FakeSTModel.__init__(self)

    fake_st.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    from backend.rag.embedder import SentenceTransformerEmbedder
    e = SentenceTransformerEmbedder("intfloat/e5-small-v2",
                                    query_prefix="query: ",
                                    passage_prefix="passage: ")
    assert e.dimension == 8
    e.embed(["some passage"])
    e.embed_query(["some question"])
    assert e._model.calls[0] == ["passage: some passage"]
    assert e._model.calls[1] == ["query: some question"]


# ------------------------------------------------- real-model (opt-in) ----

@pytest.mark.model
def test_real_minilm_embedder_smoke():
    """Opt-in (requires HF download): real ST model end-to-end."""
    st = pytest.importorskip("sentence_transformers")
    e = get_embedder("all-MiniLM-L6-v2")
    vecs = e.embed(["DoorStatusIF carries door state signals"])
    assert len(vecs[0]) == e.dimension
