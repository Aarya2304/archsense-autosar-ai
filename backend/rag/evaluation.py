"""Deterministic retrieval evaluation (M2.6).

Scores the retrieval layer against the M0 ground-truth QA pairs
(``qa_pairs`` in ``data/ground_truth/ground_truth.json``). For each question
we retrieve ``top_k`` chunks and compare retrieved provenance (pages,
sections) with the gold annotations:

- **page_hit@K**    : share of questions with >= 1 retrieved chunk whose page
                      span intersects a gold page (retrieval hit rate)
- **section_hit@K** : share of questions with >= 1 retrieved chunk whose
                      section number is in the gold section list
- **MRR_page**      : mean reciprocal rank of the first gold-page hit
- **median_latency_ms / p95_latency_ms**: per-question retrieval latency

Notes on honesty of numbers:
- Gold pages refer to the *rendered* document; chunk provenance pages come
  from the same page-aware pipeline, so page identity is well-defined.
- Some gold pages belong to signal-dictionary tables whose chunks are page
  spans that may straddle gold pages; intersection handles that.
- The evaluation embeds each question once and queries the store directly;
  no LLM is involved, results are deterministic per (embedder, corpus).

Usage (library): ``evaluate_retrieval(service, ground_truth_path, top_k)``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from backend.config import GROUND_TRUTH_DIR
from backend.rag.retriever import RetrievalService


@dataclass
class QuestionResult:
    """Outcome of evaluating one QA pair."""

    qa_id: str
    question: str
    gold_pages: list[int]
    gold_sections: list[str]
    hit_pages: list[int]            # pages of retrieved chunks (rank order)
    hit_sections: list[str]         # sections of retrieved chunks (rank order)
    page_hit: bool                  # any retrieved chunk touches a gold page
    section_hit: bool
    reciprocal_rank_page: float     # 1/rank of first gold-page hit (0 if none)
    first_hit_chunk_id: str | None
    latency_ms: float


@dataclass
class RetrievalReport:
    """Aggregate retrieval metrics + per-question detail."""

    top_k: int
    n_questions: int
    page_hit_at_k: float
    section_hit_at_k: float
    mrr_page: float
    median_latency_ms: float
    p95_latency_ms: float
    per_question: list[QuestionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "top_k": self.top_k,
            "n_questions": self.n_questions,
            "page_hit_at_k": round(self.page_hit_at_k, 4),
            "section_hit_at_k": round(self.section_hit_at_k, 4),
            "mrr_page": round(self.mrr_page, 4),
            "median_latency_ms": round(self.median_latency_ms, 1),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "per_question": [
                {
                    "qa_id": q.qa_id,
                    "question": q.question,
                    "gold_pages": q.gold_pages,
                    "gold_sections": q.gold_sections,
                    "hit_pages": q.hit_pages,
                    "hit_sections": q.hit_sections,
                    "page_hit": q.page_hit,
                    "section_hit": q.section_hit,
                    "reciprocal_rank_page": round(q.reciprocal_rank_page, 4),
                    "latency_ms": round(q.latency_ms, 1),
                }
                for q in self.per_question
            ],
        }

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2,
                                   ensure_ascii=False), encoding="utf-8")
        return path

    def summary_line(self) -> str:
        return (f"page_hit@{self.top_k}={self.page_hit_at_k:.3f}  "
                f"section_hit@{self.top_k}={self.section_hit_at_k:.3f}  "
                f"MRR_page={self.mrr_page:.3f}  "
                f"median={self.median_latency_ms:.0f}ms  "
                f"p95={self.p95_latency_ms:.0f}ms  (n={self.n_questions})")


def load_qa_pairs(ground_truth_path: Path | None = None) -> list[dict]:
    """QA pairs from ground truth, ascending by id (deterministic order)."""
    path = Path(ground_truth_path) if ground_truth_path else \
        GROUND_TRUTH_DIR / "ground_truth.json"
    gt = json.loads(path.read_text(encoding="utf-8"))
    pairs = list(gt.get("qa_pairs") or [])
    pairs.sort(key=lambda q: q.get("id", ""))
    return pairs


def evaluate_retrieval(service: RetrievalService,
                       ground_truth_path: Path | None = None,
                       top_k: int = 5,
                       max_questions: int | None = None) -> RetrievalReport:
    """Run every QA pair through the retriever and score gold-page hits."""
    qa_pairs = load_qa_pairs(ground_truth_path)
    if max_questions:
        qa_pairs = qa_pairs[:max_questions]

    results: list[QuestionResult] = []
    for qa in qa_pairs:
        t0 = time.perf_counter()
        res = service.retrieve(qa["question"], top_k=top_k)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        gold_pages = set(int(p) for p in qa.get("gold_pages") or [])
        gold_sections = set(qa.get("gold_sections") or [])

        hit_pages: list[int] = []
        hit_sections: list[str] = []
        page_hit = False
        section_hit = False
        rr = 0.0
        first_hit_id: str | None = None
        for rank, chunk in enumerate(res.chunks, start=1):
            pages = set(range(chunk.page_start, chunk.page_end + 1))
            hit_pages.extend(sorted(pages))
            hit_sections.append(chunk.section_no or "(front)")
            if not page_hit and gold_pages & pages:
                page_hit = True
                rr = 1.0 / rank
                first_hit_id = chunk.chunk_id
            if not section_hit and chunk.section_no in gold_sections:
                section_hit = True

        results.append(QuestionResult(
            qa_id=qa["id"], question=qa["question"],
            gold_pages=sorted(gold_pages),
            gold_sections=sorted(gold_sections),
            hit_pages=hit_pages, hit_sections=hit_sections,
            page_hit=page_hit, section_hit=section_hit, reciprocal_rank_page=rr,
            first_hit_chunk_id=first_hit_id, latency_ms=latency_ms))

    n = len(results)
    latencies = sorted(r.latency_ms for r in results)
    p95_idx = min(n - 1, int(round(0.95 * (n - 1))))
    return RetrievalReport(
        top_k=top_k,
        n_questions=n,
        page_hit_at_k=sum(r.page_hit for r in results) / n if n else 0.0,
        section_hit_at_k=sum(r.section_hit for r in results) / n if n else 0.0,
        mrr_page=sum(r.reciprocal_rank_page for r in results) / n if n else 0.0,
        median_latency_ms=median(latencies) if latencies else 0.0,
        p95_latency_ms=latencies[p95_idx] if latencies else 0.0,
        per_question=results,
    )
