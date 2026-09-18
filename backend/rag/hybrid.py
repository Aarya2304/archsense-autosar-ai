"""Hybrid retrieval: BM25 lexical + dense vectors fused with RRF (M3.2).

Motivation (M2 finding, D-014): on this corpus the lexical baseline beat
every dense model on page-hit@5, because QA vocabulary matches document
vocabulary. Dense retrieval contributes paraphrase robustness lexical
matching lacks. Hybrid retrieval runs BOTH retrievers over the same
filtered corpus and fuses their rankings with Reciprocal Rank Fusion:

    RRF(d) = sum over lists containing d of 1 / (rrf_k + rank(d))

with ranks starting at 1. ``rrf_k`` defaults to 60 (Cormack et al. 2009);
it is configurable but deliberately NOT tuned against the QA set (D-018).

Fusion is rank-based: raw BM25 and cosine scores are never mixed, so no
score-scale normalization is needed and fusion stays deterministic. A chunk
appearing in both lists accumulates from both; a chunk in only one list
still contributes (absent lists add 0). Ties break deterministically by
(document_name, chunk_id).

The lexical side is served by an in-memory BM25 index rebuilt whenever the
vector store's chunk count changes (``ensure_index``), so reset/re-index
cycles keep lexical results consistent automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.rag.embedder import EmbeddingProvider
from backend.rag.lexical import LexicalRetriever
from backend.rag.models import RetrievedChunk, RetrievalResult
from backend.rag.retriever import RetrievalService, _FILTERABLE
from backend.rag.vector_store import VectorStore


def rrf_fuse(rankings: list[list[RetrievedChunk]], rrf_k: int = 60,
             top_k: int = 10) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion over ordered result lists.

    ``rankings[i]`` is a ranked list (best first) from retrieval method
    ``i`` (list 0 = lexical, list 1 = dense). Returns the fused ranking
    (best first), capped at ``top_k``. Every hit keeps its provenance;
    fused hits carry ``rrf_score``, a ``sources`` tag naming the lists that
    contained them, and per-list ranks (``lexical_rank``/``dense_rank``)
    when present.

    Deterministic: equal fused scores tie-break by (document_name,
    chunk_id), so the ordering never depends on dict iteration order.
    """
    scores: dict[str, float] = {}
    best: dict[str, RetrievedChunk] = {}
    rank_in: dict[str, dict[str, int]] = {"lexical": {}, "dense": {}}

    for list_idx, ranking in enumerate(rankings):
        list_name = ("lexical", "dense")[min(list_idx, 1)]
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + \
                1.0 / (rrf_k + rank)
            rank_in[list_name][hit.chunk_id] = rank
            if hit.chunk_id not in best:
                best[hit.chunk_id] = hit

    fused: list[RetrievedChunk] = []
    for chunk_id, score in scores.items():
        hit = best[chunk_id]
        in_lex = chunk_id in rank_in["lexical"]
        in_dense = chunk_id in rank_in["dense"]
        sources = "dense+lexical" if (in_lex and in_dense) else \
            ("lexical" if in_lex else "dense")
        fused.append(RetrievedChunk(
            chunk_id=hit.chunk_id,
            text=hit.text,
            distance=hit.distance,
            similarity=hit.similarity,
            document_name=hit.document_name,
            version=hit.version,
            section_no=hit.section_no,
            section_title=hit.section_title,
            page_start=hit.page_start,
            page_end=hit.page_end,
            chunk_type=hit.chunk_type,
            chunk_seq=hit.chunk_seq,
            token_count=hit.token_count,
            lexical_score=hit.lexical_score,
            lexical_rank=rank_in["lexical"].get(chunk_id),
            dense_rank=rank_in["dense"].get(chunk_id),
            rrf_score=score,
            sources=sources,
        ))

    fused.sort(key=lambda c: (-c.rrf_score, c.document_name, c.chunk_id))
    return fused[:top_k]


# -------------------------------------------------------------- filters ---

def _split_filters(filters: dict[str, Any]) -> tuple[dict[str, Any],
                                                     dict[str, Any]]:
    """Split filters into store-level (equality) and post-filter keys.

    Same semantics as M2: equality keys filter inside the store/page-in is
    a client-side post-filter; unknown keys raise (typo protection).
    """
    where: dict[str, Any] = {}
    post: dict[str, Any] = {}
    for key, value in (filters or {}).items():
        if key == "page_in":
            post[key] = value
        elif key in _FILTERABLE:
            where[key] = value
        else:
            raise ValueError(
                f"Unsupported retrieval filter key: {key!r} "
                f"(supported: {sorted(_FILTERABLE | {'page_in'})})")
    return where, post


def _wanted_pages(post: dict[str, Any]) -> set[int] | None:
    page_in = post.get("page_in")
    if page_in is None:
        return None
    if isinstance(page_in, (tuple, list)) and len(page_in) == 2:
        return set(range(int(page_in[0]), int(page_in[1]) + 1))
    return {int(page_in)}


def _post_filter(hits: list[RetrievedChunk],
                 post: dict[str, Any]) -> list[RetrievedChunk]:
    """Apply the M2 ``page_in`` post-filter semantics to any hit list."""
    wanted = _wanted_pages(post)
    if wanted is None:
        return hits
    return [h for h in hits
            if wanted & set(range(h.page_start, h.page_end + 1))]


def _equality_filter(hits: list[RetrievedChunk],
                     where: dict[str, Any]) -> list[RetrievedChunk]:
    """Client-side equality filter mirroring the store-level ``where`` keys.

    Needed for the lexical mirror leg: BM25 retrieval has no metadata store
    filter, so equality keys (``version``, ``document_name``, ...) applied
    only to the dense leg would leak other documents'/versions' chunks into
    the fused results (M8 compatibility fix: same semantics, both legs).
    """
    if not where:
        return hits
    return [h for h in hits
            if all(getattr(h, key, None) == value
                   for key, value in where.items())]


# ---------------------------------------------------------------- service --

@dataclass
class HybridRetrievalService:
    """BM25 + dense retrieval fused with RRF (default M3 retrieval mode).

    ``dense`` is the existing M2 ``RetrievalService`` (unchanged, so
    dense-only mode remains available via ``mode="dense"`` and through the
    wrapped service itself). The lexical index mirrors the vector store and
    is rebuilt lazily whenever the store's chunk count changes.
    """

    dense: RetrievalService
    rrf_k: int = 60
    lexical_k: int = 20
    dense_k: int = 20
    default_top_k: int = 5
    call_log: list[dict[str, Any]] = field(default_factory=list)
    _lexical: LexicalRetriever = field(default_factory=LexicalRetriever,
                                       init=False, repr=False)
    _lexical_n: int = field(default=-1, init=False, repr=False)

    # ------------------------------------------------------------- index --
    def ensure_index(self, force: bool = False) -> None:
        """(Re)build the BM25 index from the store when stale.

        Re-indexing the same corpus (same count) does not rebuild; after
        ``store.reset()`` or a re-index with a different count it does.
        ``force=True`` rebuilds unconditionally (tests).
        """
        n = self.dense.store.count()
        if force or n != self._lexical_n:
            chunks = sorted(self.dense.store.get_all_chunks(),
                            key=lambda c: (c.document_name, c.chunk_seq,
                                           c.chunk_id))
            self._lexical.build(chunks)
            self._lexical_n = n

    # ---------------------------------------------------------- retrieve --
    def retrieve(self, query: str, top_k: int | None = None,
                 filters: dict[str, Any] | None = None,
                 mode: str = "hybrid") -> RetrievalResult:
        """Retrieve results for ``query``.

        ``mode``: "hybrid" (default) | "dense" | "lexical". All modes share
        M2 filter semantics. Dense-only mode delegates to the wrapped
        ``RetrievalService`` unchanged, preserving M2 behaviour exactly.
        """
        if mode not in {"hybrid", "dense", "lexical"}:
            raise ValueError(f"Unknown retrieval mode: {mode!r}")
        k = top_k if top_k is not None else self.default_top_k
        filters = dict(filters or {})
        where, post = _split_filters(filters)

        if mode == "dense":
            result = self.dense.retrieve(query, top_k=k, filters=filters)
            self._log(query, mode, k, filters, len(result.chunks))
            return result

        self.ensure_index()
        lex_pool = _equality_filter(
            _post_filter(
                self._lexical.retrieve(query, top_k=max(self.lexical_k, k)),
                post),
            where)

        if mode == "lexical":
            trimmed = lex_pool[:k]
            result = RetrievalResult(query=query, chunks=trimmed, top_k=k,
                                     filters=filters, mode="lexical")
            self._log(query, mode, k, filters, len(trimmed))
            return result

        # --- hybrid: dense over the (filtered) store + lexical over the
        # mirror corpus, fused with RRF, then post-filter + trim.
        fetch_k = max(self.dense_k, k * 4) if post else max(self.dense_k, k)
        dense_hits = self.dense.store.query(
            self.dense.embedder.embed_query([query])[0], top_k=fetch_k,
            where=where or None)
        dense_hits = _post_filter(dense_hits, post)

        fused = rrf_fuse([lex_pool, dense_hits], rrf_k=self.rrf_k,
                         top_k=max(k, self.dense_k, self.lexical_k))
        result = RetrievalResult(query=query, chunks=fused[:k], top_k=k,
                                 filters=filters, mode="hybrid",
                                 dense_k=self.dense_k,
                                 lexical_k=self.lexical_k)
        self._log(query, mode, k, filters, len(result.chunks))
        return result

    def _log(self, query: str, mode: str, top_k: int,
             filters: dict[str, Any], n_hits: int) -> None:
        self.call_log.append({"query": query, "mode": mode, "top_k": top_k,
                              "filters": filters, "n_hits": n_hits})


def get_hybrid_service(embedder: EmbeddingProvider | None = None,
                       store: VectorStore | None = None,
                       **kwargs) -> HybridRetrievalService:
    """Factory mirroring M2 conventions (defaults from backend.config)."""
    from backend.config import HYBRID_DENSE_K, HYBRID_LEXICAL_K, HYBRID_RRF_K

    if embedder is None or store is None:
        from backend.rag.embedder import get_embedder
        from backend.rag.vector_store import get_vector_store

        embedder = embedder or get_embedder()
        store = store or get_vector_store()
    return HybridRetrievalService(
        dense=RetrievalService(embedder=embedder, store=store),
        rrf_k=kwargs.pop("rrf_k", HYBRID_RRF_K),
        lexical_k=kwargs.pop("lexical_k", HYBRID_LEXICAL_K),
        dense_k=kwargs.pop("dense_k", HYBRID_DENSE_K),
        **kwargs)
