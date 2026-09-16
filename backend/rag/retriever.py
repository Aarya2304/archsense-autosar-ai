"""Retrieval service (M2.4): embed query -> filtered similarity search.

The M3 copilot consumes ``RetrievalService.retrieve`` results; this layer
adds no LLM behaviour. Metadata filters map to Chroma ``where`` clauses
equality filters on indexed fields (document_name, version, chunk_type,
section_no); unsupported keys raise so callers notice typos early.
Post-filters (page ranges) run client-side over the fetched hits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.rag.embedder import EmbeddingProvider
from backend.rag.models import RetrievalResult
from backend.rag.vector_store import VectorStore

# Metadata keys a caller may filter on (equality). Everything else must go
# through post-filtering; unknown keys are rejected to surface typos.
_FILTERABLE = {"document_name", "version", "section_no", "chunk_type",
               "sha256"}


@dataclass
class RetrievalService:
    """Metadata-aware similarity retrieval over the vector store."""

    embedder: EmbeddingProvider
    store: VectorStore
    default_top_k: int = 5
    call_log: list[dict[str, Any]] = field(default_factory=list)

    def retrieve(self, query: str, top_k: int | None = None,
                 filters: dict[str, Any] | None = None) -> RetrievalResult:
        """Retrieve the ``top_k`` most similar chunks for ``query``.

        ``filters`` supports equality on {document_name, version,
        section_no, chunk_type, sha256} plus the client-side key
        ``page_in`` (a page number or (low, high) inclusive range that must
        intersect a chunk's page span).
        """
        k = top_k if top_k is not None else self.default_top_k
        filters = dict(filters or {})

        where, post = self._split_filters(filters)
        # embed_query (not embed): asymmetric models apply their query
        # prefix here, keeping corpus/query spaces aligned.
        embedding = self.embedder.embed_query([query])[0]
        # Over-fetch when post-filters may drop hits.
        fetch_k = k * 4 if post else k
        hits = self.store.query(embedding, top_k=fetch_k, where=where or None)
        if post:
            hits = [h for h in hits if self._matches_post(h, post)][:k]

        result = RetrievalResult(query=query, chunks=hits, top_k=k,
                                 filters=filters)
        self.call_log.append({
            "query": query, "top_k": k, "filters": filters,
            "n_hits": len(hits),
        })
        return result

    # ------------------------------------------------------------ helpers --
    @staticmethod
    def _split_filters(filters: dict[str, Any]) -> tuple[dict[str, Any],
                                                         dict[str, Any]]:
        where: dict[str, Any] = {}
        post: dict[str, Any] = {}
        for key, value in filters.items():
            if key == "page_in":
                post[key] = value
            elif key in _FILTERABLE:
                where[key] = value
            else:
                raise ValueError(
                    f"Unsupported retrieval filter key: {key!r} "
                    f"(supported: {sorted(_FILTERABLE | {'page_in'})})")
        return where, post

    @staticmethod
    def _matches_post(hit: Any, post: dict[str, Any]) -> bool:
        pages = set(range(hit.page_start, hit.page_end + 1))
        page_in = post.get("page_in")
        if page_in is not None:
            if isinstance(page_in, (tuple, list)) and len(page_in) == 2:
                lo, hi = page_in
                if not pages & set(range(int(lo), int(hi) + 1)):
                    return False
            else:
                if int(page_in) not in pages:
                    return False
        return True
