"""Embedding providers (M2.2).

Two implementations behind one small protocol (D-013):

- ``HashingEmbedder``: dependency-free, deterministic 512-dim character
  n-gram hashing embedding. Zero-download, zero-network, fast enough for
  unit tests and for CI. Deliberately semantic-free: it is a *correctness*
  baseline, not a quality baseline (exact-token overlap still retrieves
  plausibly because it hashes shared n-grams).
- ``SentenceTransformerEmbedder``: real semantic embeddings via the
  ``sentence-transformers`` package (lazy import; only needed when selected
  or when the benchmark runs). Candidate models are configured in
  ``backend.config.EMBEDDING_MODEL_CANDIDATES``.

The rest of the application depends only on ``EmbeddingProvider``, never on
a concrete class, so swapping models is a config change (benchmark-driven,
see docs/PROJECT_DECISIONS.md D-014 once the benchmark has run).
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Anything that can turn texts into fixed-length float vectors."""

    @property
    def name(self) -> str:
        """Stable identifier (used in metadata + benchmark results)."""
        ...

    @property
    def dimension(self) -> int:
        """Embedding dimensionality."""
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a sequence of texts (order preserved; passage semantics)."""
        ...

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed query texts (may add a model-specific query prefix)."""
        ...


# --------------------------------------------------------------------------
# Deterministic hashing embedder (tests / offline fallback)
# --------------------------------------------------------------------------

_NGRAM_RE = re.compile(r"[a-z0-9]+")
_TOKEN_DIM = 512


@dataclass
class HashingEmbedder:
    """Deterministic character n-gram hashing embedding (no dependencies).

    Text is lowercased; word and 3-gram character features are hashed into
    ``_TOKEN_DIM`` buckets with sign hashing; the vector is L2-normalized so
    cosine similarity behaves like plain dot product. Deterministic across
    runs and machines (md5-based hashing, no randomized state).
    """

    dim: int = _TOKEN_DIM

    @property
    def name(self) -> str:
        return f"hashing-{self.dim}"

    @property
    def dimension(self) -> int:
        return self.dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed(texts)

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        norm = _NGRAM_RE
        tokens = norm.findall(text.lower())
        features: list[str] = []
        features.extend(f"w:{tok}" for tok in tokens)
        squashed = " ".join(tokens)
        for i in range(max(0, len(squashed) - 2)):
            features.append(f"c:{squashed[i:i + 3]}")
        for feat in features:
            digest = hashlib.md5(feat.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        l2 = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / l2 for v in vec]


# --------------------------------------------------------------------------
# Sentence-Transformers embedder (production candidates)
# --------------------------------------------------------------------------

# Normalization is applied by ST (models are loaded with normalize_embeddings
# semantics handled at call time), so cosine distance in ChromaDB is valid.


class SentenceTransformerEmbedder:
    """Embedder backed by a sentence-transformers model (lazy import).

    ``query_prefix``/``passage_prefix`` support asymmetric models such as
    the E5 family (which requires ``query: `` / ``passage: `` prefixes); the
    default is symmetric (no prefixes).
    """

    def __init__(self, model_name: str, device: str | None = None,
                 batch_size: int = 32,
                 query_prefix: str = "", passage_prefix: str = "") -> None:
        from sentence_transformers import SentenceTransformer  # lazy

        self._model_name = model_name
        self._batch_size = batch_size
        self._query_prefix = query_prefix
        self._passage_prefix = passage_prefix
        kwargs: dict = {}
        if device:
            kwargs["device"] = device
        self._model = SentenceTransformer(model_name, **kwargs)
        # sentence-transformers >= 5 renamed the dimension getter.
        get_dim = getattr(self._model, "get_embedding_dimension", None)
        if get_dim is None:
            get_dim = self._model.get_sentence_embedding_dimension
        self._dim = int(get_dim())

    @property
    def name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            [f"{self._passage_prefix}{t}" for t in texts],
            batch_size=self._batch_size,
            normalize_embeddings=True,   # cosine-ready, deterministic
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            [f"{self._query_prefix}{t}" for t in texts],
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    def parameter_count(self) -> int:
        """Total model parameters (for size estimation in the benchmark)."""
        return sum(p.numel() for p in self._model.parameters())


# --------------------------------------------------------------------------
# Factory + timing helper (used by the benchmark)
# --------------------------------------------------------------------------


# Asymmetric-model prefix conventions (benchmark + factory support).
_PREFIX_CONVENTIONS: dict[str, tuple[str, str]] = {
    "intfloat/e5": ("query: ", "passage: "),   # match by model-name prefix
}


def _prefixes_for(model_name: str) -> tuple[str, str]:
    for key, prefixes in _PREFIX_CONVENTIONS.items():
        if model_name.startswith(key):
            return prefixes
    return ("", "")


def get_embedder(name: str | None = None) -> EmbeddingProvider:
    """Resolve an embedder by name; ``None`` -> configured default.

    Names starting with ``hashing`` return the deterministic hash embedder;
    anything else is treated as a sentence-transformers model id.
    """
    from backend.config import EMBEDDING_MODEL

    resolved = name or EMBEDDING_MODEL
    if resolved.startswith("hashing"):
        return HashingEmbedder()
    q_pref, p_pref = _prefixes_for(resolved)
    return SentenceTransformerEmbedder(resolved, query_prefix=q_pref,
                                       passage_prefix=p_pref)


@dataclass
class EmbeddingTiming:
    """Wall-clock embedding throughput measurement (single-run)."""

    model_name: str
    n_texts: int
    total_seconds: float

    @property
    def texts_per_second(self) -> float:
        return self.n_texts / self.total_seconds if self.total_seconds else 0.0


def timed_embed(provider: EmbeddingProvider,
                texts: Sequence[str]) -> tuple[list[list[float]], EmbeddingTiming]:
    """Embed ``texts`` while measuring wall-clock time (M2.2 metric)."""
    t0 = time.perf_counter()
    vectors = provider.embed(texts)
    elapsed = time.perf_counter() - t0
    timing = EmbeddingTiming(
        model_name=provider.name, n_texts=len(texts), total_seconds=elapsed)
    return vectors, timing


def estimate_model_size_mb(provider: EmbeddingProvider) -> float | None:
    """Parameter-based weight-size estimate in MB (None if not measurable).

    float32 assumed (4 bytes/parameter) -- a reasonable, documented estimate
    for CPU inference; avoids hard dependencies on model-file layout.
    """
    pc = getattr(provider, "parameter_count", None)
    if pc is None:
        return None
    return round(pc() * 4 / (1024 * 1024), 1)
