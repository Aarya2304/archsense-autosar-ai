"""Embedding-model benchmark framework (M2.2).

Compares candidate embedding models on THIS project's corpus before a
default is chosen (plan requirement: no model picked by popularity). All
numbers come from deterministic single-machine runs and are written to
``data/evaluation/embedding_benchmark.json`` (git-ignored).

Per model, measures:
- dimensionality + parameter-based size estimate (float32 MB)
- corpus embedding throughput (texts/s over the real chunk corpus)
- retrieval quality on the M0 QA set at K in {1, 3, 5, 10}: page/section
  hit rates + MRR (via :mod:`backend.rag.evaluation` metrics logic)

Methodology notes (documented in docs/PROJECT_DECISIONS.md D-014):
- every model embeds the SAME chunk corpus (deterministic chunker) into its
  own fresh in-memory store, so quality differences are model-only;
- latency numbers are wall-clock on the developer laptop (CPU vs GPU is
  recorded but not normalized) -- relative, not absolute, measurements;
- the deterministic ``hashing`` embedder is included as a sanity floor:
  any real model must beat it clearly on hit-rate metrics.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.config import EVAL_DIR, PROCESSED_DIR
from backend.rag.chunker import chunk_ingestion_result
from backend.rag.evaluation import load_qa_pairs
from backend.rag.embedder import (EmbeddingProvider, estimate_model_size_mb,
                                  timed_embed)
from backend.rag.vector_store import ChromaVectorStore, StoredChunk


@dataclass
class BenchmarkRow:
    """One candidate model's full benchmark result."""

    model: str
    dimension: int
    size_estimate_mb: float | None
    corpus_texts: int
    embed_seconds: float
    texts_per_second: float
    metrics_by_k: dict[int, dict[str, float]] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "model": self.model,
            "dimension": self.dimension,
            "size_estimate_mb": self.size_estimate_mb,
            "corpus_texts": self.corpus_texts,
            "embed_seconds": round(self.embed_seconds, 2),
            "texts_per_second": round(self.texts_per_second, 1),
            "metrics_by_k": {
                str(k): {m: round(v, 4) for m, v in mets.items()}
                for k, mets in self.metrics_by_k.items()
            },
        }
        if self.error:
            d["error"] = self.error
        return d


def _load_corpus_chunks(processed_dir: Path, max_chunks: int | None):
    """Deterministic chunk corpus from all saved ingestion records."""
    paths = sorted(processed_dir.glob("*__processed.json"))
    if not paths:
        raise FileNotFoundError(
            f"No *__processed.json files under {processed_dir}. Run "
            f"scripts/process_sample_docs.py first.")
    chunks = []
    for p in paths:
        payload = json.loads(p.read_text(encoding="utf-8"))
        chunks.extend(chunk_ingestion_result(payload))
    if max_chunks:
        chunks = chunks[:max_chunks]
    return chunks


class _ClientSideCosineStore:
    """Tiny in-memory cosine store used to isolate model quality.

    Chroma is deliberately avoided here: brute-force cosine over a few
    hundred chunks is exact (no ANN approximation) and keeps each model's
    benchmark independent of index internals.
    """

    def __init__(self) -> None:
        self._vecs: list[list[float]] = []
        self._chunks: list[StoredChunk] = []

    def upsert_chunks(self, chunks, embeddings) -> int:
        self._vecs = [list(map(float, e)) for e in embeddings]
        self._chunks = list(chunks)
        return len(self._chunks)

    def count(self) -> int:
        return len(self._chunks)

    def query(self, embedding, top_k: int = 5, where=None) -> list:
        from backend.rag.vector_store import _decode_pages_csv
        sims = []
        q = list(map(float, embedding))
        for i, v in enumerate(self._vecs):
            num = sum(a * b for a, b in zip(q, v))
            sims.append((num, i))  # vectors are L2-normalized -> dot = cosine
        sims.sort(key=lambda t: -t[0])
        hits = []
        for sim, i in sims[:top_k]:
            c = self._chunks[i]
            meta = c.metadata
            ps, pe = _decode_pages_csv(meta)
            from backend.rag.models import RetrievedChunk
            hits.append(RetrievedChunk(
                chunk_id=c.chunk_id, text=c.text,
                distance=1.0 - sim, similarity=sim,
                document_name=str(meta.get("document_name", "")),
                version=str(meta.get("version", "")),
                section_no=str(meta.get("section_no", "")),
                section_title=str(meta.get("section_title", "")),
                page_start=ps, page_end=pe,
                chunk_type=str(meta.get("chunk_type", "")),
                chunk_seq=int(meta.get("chunk_seq", 0) or 0),
                token_count=int(meta.get("token_count", 0) or 0)))
        return hits

    def reset(self) -> None:
        self._vecs, self._chunks = [], []


def _score_questions(provider: EmbeddingProvider, chunks, qa_pairs,
                     ks: list[int]) -> dict[int, dict[str, float]]:
    """Client-side quality scoring reusing evaluation-metric definitions."""
    passage_vecs = provider.embed([c.text for c in chunks])
    store = _ClientSideCosineStore()
    stored = [StoredChunk(chunk_id=c.chunk_id, text=c.text,
                          metadata=c.metadata()) for c in chunks]
    store.upsert_chunks(stored, passage_vecs)

    out: dict[int, dict[str, float]] = {}
    max_k = max(ks)
    page_hits = {k: 0 for k in ks}
    section_hits = {k: 0 for k in ks}
    rr_sums = {k: 0.0 for k in ks}
    for qa in qa_pairs:
        q_vec = provider.embed_query([qa["question"]])[0]
        ranked = store.query(q_vec, top_k=max_k)
        gold_pages = set(int(p) for p in qa.get("gold_pages") or [])
        gold_sections = set(qa.get("gold_sections") or [])
        for k in ks:
            cut = ranked[:k]
            ph = sh = False
            rr = 0.0
            for rank, hit in enumerate(cut, start=1):
                pages = set(range(hit.page_start, hit.page_end + 1))
                if not ph and gold_pages & pages:
                    ph, rr = True, 1.0 / rank
                if not sh and hit.section_no in gold_sections:
                    sh = True
            page_hits[k] += ph
            section_hits[k] += sh
            rr_sums[k] += rr
    n = len(qa_pairs) or 1
    for k in ks:
        out[k] = {
            "page_hit": page_hits[k] / n,
            "section_hit": section_hits[k] / n,
            "mrr_page": rr_sums[k] / n,
        }
    return out


def run_benchmark(model_names: list[str],
                  processed_dir: Path = PROCESSED_DIR,
                  output_path: Path | None = None,
                  max_chunks: int | None = None,
                  ks: list[int] | None = None) -> dict[str, Any]:
    """Benchmark candidate models; persist + return the machine-readable report."""
    ks = ks or [1, 3, 5, 10]
    chunks = _load_corpus_chunks(Path(processed_dir), max_chunks)
    qa_pairs = load_qa_pairs()
    print(f"corpus: {len(chunks)} chunks from {PROCESSED_DIR} | "
          f"{len(qa_pairs)} QA questions | K in {ks}")

    rows: list[BenchmarkRow] = []
    for name in model_names:
        print(f"--- benchmarking {name} ...")
        row = BenchmarkRow(model=name, dimension=0, size_estimate_mb=None,
                           corpus_texts=len(chunks), embed_seconds=0.0,
                           texts_per_second=0.0)
        try:
            from backend.rag.embedder import get_embedder
            provider = get_embedder(name)
            row.dimension = provider.dimension
            row.size_estimate_mb = estimate_model_size_mb(provider)
            vectors, timing = timed_embed(provider, [c.text for c in chunks])
            row.embed_seconds = timing.total_seconds
            row.texts_per_second = timing.texts_per_second
            row.metrics_by_k = _score_questions(provider, chunks, qa_pairs, ks)
        except Exception as exc:            # keep benchmarking other candidates
            row.error = f"{type(exc).__name__}: {exc}"
            print(f"    [ERROR] {row.error}")
        rows.append(row)

    report = {
        "corpus": {
            "processed_dir": str(PROCESSED_DIR),
            "n_chunks": len(chunks),
            "n_questions": len(qa_pairs),
            "ks": ks,
        },
        "models": [r.to_dict() for r in rows],
    }
    out = Path(output_path) if output_path else \
        EVAL_DIR / "embedding_benchmark.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"[OK] benchmark written to {out}")
    return report


# Re-export so the benchmark script (and tests) can exercise the real
# Chroma-backed path too if desired.
__all__ = ["BenchmarkRow", "ChromaVectorStore", "run_benchmark"]
