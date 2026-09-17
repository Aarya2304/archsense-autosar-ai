"""Deterministic in-memory lexical retrieval (M3.1).

A compact, dependency-free Okapi BM25 implementation over the chunk corpus.
Chosen over adding a search dependency (rank-bm25, whoosh, Elasticsearch)
because the corpus is ~280 chunks: an in-memory index builds in
milliseconds, is trivially deterministic (integer tie-breaking on equal
scores), and keeps the MVP free of unnecessary infrastructure (D-006).

BM25 parameters: ``k1=1.5``, ``b=0.75`` (standard defaults). Tokenization
is the same lowercase ``[a-z0-9]+`` word split the M2 hashing embedder
uses, plus a tiny static stop-word list, so lexical and dense retrieval see
consistent text. Documents are stored as provenance-carrying
``RetrievedChunk`` objects, so lexical hits flow into RRF fusion (M3.2)
without any representation change.

Determinism: scores are pure float arithmetic over the index; ties are
broken by (document_name, chunk_id) so equal-scoring documents always come
out in a stable order regardless of dict/set iteration order.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from backend.rag.models import RetrievedChunk

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Minimal static stop list (no corpus-derived thresholds -> fully
# deterministic and identical across corpora).
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "its", "of", "on", "or", "that", "the", "to",
    "was", "were", "what", "which", "who", "with",
})

# Okapi BM25 defaults.
K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    """Lowercase word tokenization shared by index and query side."""
    return [t for t in _TOKEN_RE.findall(text.lower())
            if t not in _STOPWORDS]


def stem_variants(term: str) -> set[str]:
    """Light deterministic suffix-stripping variants of ``term``.

    Maps morphological forms onto each other (provides -> provide,
    policies -> policy) without a dictionary. Shared by the M3.11 gate's
    coverage check and the mock provider's relevance heuristic so both
    treat morphology identically.
    """
    variants = {term}
    if term.endswith("ies") and len(term) > 4:
        variants.add(term[:-3] + "y")
    if term.endswith("es") and len(term) > 3:
        variants.add(term[:-2])
    if term.endswith("s") and len(term) > 3:
        variants.add(term[:-1])
    if term.endswith("ed") and len(term) > 4:
        variants.add(term[:-2])
        variants.add(term[:-1])
    if term.endswith("ing") and len(term) > 5:
        variants.add(term[:-3])
        variants.add(term[:-3] + "e")
    return variants


@dataclass
class LexicalRetriever:
    """In-memory BM25 over ``RetrievedChunk`` documents (rebuilt per corpus).

    Deterministic: iteration order of the index follows insertion order,
    and equal scores tie-break by (document_name, chunk_id).
    """

    chunks: list[RetrievedChunk] = field(default_factory=list)
    k1: float = K1
    b: float = B

    # Derived index state (built lazily on first query or explicit build).
    _doc_tokens: list[list[str]] = field(default_factory=list, init=False,
                                         repr=False)
    _doc_len: list[float] = field(default_factory=list, init=False, repr=False)
    _df: Counter = field(default_factory=Counter, init=False, repr=False)
    _built: bool = field(default=False, init=False, repr=False)

    @property
    def document_frequency(self) -> dict[str, int]:
        """Read-only view of term -> document frequency (gate consumers)."""
        return dict(self._df)

    # ------------------------------------------------------------- build --
    def build(self, chunks: Iterable[RetrievedChunk]) -> "LexicalRetriever":
        """(Re)index ``chunks`` in order; returns self (chainable)."""
        self.chunks = list(chunks)
        self._doc_tokens = []
        self._doc_len = []
        df: Counter = Counter()
        for c in self.chunks:
            toks = tokenize(c.text)
            self._doc_tokens.append(toks)
            self._doc_len.append(float(len(toks)))
            for term in set(toks):
                df[term] += 1
        self._df = df
        self._built = True
        return self

    def _ensure_built(self) -> None:
        if not self._built:
            self.build(self.chunks)

    # ----------------------------------------------------- tokenization --
    def _tokenize_public(self, text: str) -> list[str]:
        """Module tokenizer exposed for gate/coverage consumers (M3.11)."""
        return tokenize(text)

    # ------------------------------------------------------------ query --
    def retrieve(self, query: str, top_k: int = 10) -> list[RetrievedChunk]:
        """Return up to ``top_k`` chunks ranked by BM25 (best first).

        Hits carry ``lexical_score`` (BM25, unnormalized) and
        ``lexical_rank``; ``similarity`` is a bounded [0,1)-like value
        (BM25 score mapped through ``score / (score + k1)``) so gate logic
        and UI can treat all modes uniformly. Provenance is preserved.
        """
        self._ensure_built()
        n_docs = len(self.chunks)
        if n_docs == 0 or top_k <= 0:
            return []
        avgdl = (sum(self._doc_len) / n_docs) if n_docs else 0.0
        q_terms = tokenize(query)
        if not q_terms or avgdl == 0.0:
            return []

        scored: list[tuple[float, int]] = []
        for i, toks in enumerate(self._doc_tokens):
            tf = Counter(toks)
            score = 0.0
            for term in q_terms:
                f = tf.get(term)
                if not f:
                    continue
                df_t = self._df.get(term, 0)
                idf = math.log(1.0 + (n_docs - df_t + 0.5) / (df_t + 0.5))
                denom = f + self.k1 * (1.0 - self.b
                                       + self.b * (self._doc_len[i] / avgdl))
                score += idf * (f * (self.k1 + 1.0)) / denom
            if score > 0.0:
                scored.append((score, i))

        # Deterministic ordering: descending score, ties broken by
        # (document_name, chunk_id) so equal scores never flip between runs.
        scored.sort(key=lambda t: (-t[0],
                                   self.chunks[t[1]].document_name,
                                   self.chunks[t[1]].chunk_id))

        hits: list[RetrievedChunk] = []
        for rank, (score, i) in enumerate(scored[:top_k], start=1):
            c = self.chunks[i]
            hits.append(RetrievedChunk(
                chunk_id=c.chunk_id,
                text=c.text,
                distance=0.0,          # lexical mode has no cosine distance
                similarity=score / (score + self.k1),
                document_name=c.document_name,
                version=c.version,
                section_no=c.section_no,
                section_title=c.section_title,
                page_start=c.page_start,
                page_end=c.page_end,
                chunk_type=c.chunk_type,
                chunk_seq=c.chunk_seq,
                token_count=c.token_count,
                lexical_score=score,
                lexical_rank=rank,
                sources="lexical",
            ))
        return hits
