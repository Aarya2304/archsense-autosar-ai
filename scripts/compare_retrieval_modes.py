#!/usr/bin/env python
"""
Compare retrieval modes on the ground-truth QA set (M3.3).

    python scripts/compare_retrieval_modes.py [--model all-MiniLM-L6-v2]

Runs lexical-only, dense-only and hybrid (RRF) retrieval over the same
corpus and QA questions, and writes
``data/evaluation/retrieval_modes_comparison.json`` (git-ignored) with
page_hit@{1,3,5}, section_hit@{1,3,5}, MRR_page and latency per mode, plus
per-question detail. Deterministic per (model, corpus); no LLM involved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import EVAL_DIR  # noqa: E402
from backend.rag.evaluation import load_qa_pairs  # noqa: E402
from backend.rag.embedder import get_embedder  # noqa: E402
from backend.rag.hybrid import get_hybrid_service  # noqa: E402
from backend.rag.models import RetrievalResult  # noqa: E402
from backend.rag.vector_store import get_vector_store  # noqa: E402


def score_mode(service, mode: str, qa_pairs: list[dict], ks: list[int]):
    """Score one retrieval mode over all QA pairs at the given K values."""
    import time

    max_k = max(ks)
    n = len(qa_pairs) or 1
    page_hits = {k: 0 for k in ks}
    section_hits = {k: 0 for k in ks}
    rr_sums = {k: 0.0 for k in ks}
    per_question = []
    latencies = []

    for qa in qa_pairs:
        t0 = time.perf_counter()
        res: RetrievalResult = service.retrieve(qa["question"], top_k=max_k,
                                                mode=mode)
        latencies.append((time.perf_counter() - t0) * 1000.0)

        gold_pages = set(int(p) for p in qa.get("gold_pages") or [])
        gold_sections = set(qa.get("gold_sections") or [])
        row = {"qa_id": qa["id"], "question": qa["question"],
               "retrieved_sections": [], "page_hit_by_k": {},
               "section_hit_by_k": {}, "rr_by_k": {}}
        for k in ks:
            cut = res.chunks[:k]
            ph = sh = False
            rr = 0.0
            for rank, c in enumerate(cut, start=1):
                pages = set(range(c.page_start, c.page_end + 1))
                if not ph and gold_pages & pages:
                    ph, rr = True, 1.0 / rank
                if not sh and c.section_no in gold_sections:
                    sh = True
            page_hits[k] += ph
            section_hits[k] += sh
            rr_sums[k] += rr
            row["page_hit_by_k"][k] = ph
            row["section_hit_by_k"][k] = sh
            row["rr_by_k"][k] = round(rr, 4)
        row["retrieved_sections"] = [c.section_no or "(front)"
                                     for c in res.chunks[:max_k]]
        per_question.append(row)

    metrics = {}
    for k in ks:
        metrics[f"page_hit@{k}"] = round(page_hits[k] / n, 4)
        metrics[f"section_hit@{k}"] = round(section_hits[k] / n, 4)
        metrics[f"mrr_page@{k}"] = round(rr_sums[k] / n, 4)
    latencies.sort()
    metrics["median_latency_ms"] = round(latencies[len(latencies) // 2], 1) \
        if latencies else 0.0
    return metrics, per_question


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=None,
                    help="Embedding model (default: config EMBEDDING_MODEL)")
    ap.add_argument("--out", default=str(EVAL_DIR /
                                         "retrieval_modes_comparison.json"))
    args = ap.parse_args()

    qa_pairs = load_qa_pairs()
    service = get_hybrid_service(embedder=get_embedder(args.model),
                                 store=get_vector_store())
    print(f"corpus: {service.dense.store.count()} chunks | "
          f"{len(qa_pairs)} QA questions")

    ks = [1, 3, 5]
    modes = {}
    for mode in ("lexical", "dense", "hybrid"):
        print(f"--- scoring mode={mode} ...")
        metrics, per_question = score_mode(service, mode, qa_pairs, ks)
        modes[mode] = {"metrics": metrics, "per_question": per_question}
        print("   ", {k: v for k, v in metrics.items()})

    report = {
        "model": args.model or "config:EMBEDDING_MODEL",
        "n_questions": len(qa_pairs),
        "corpus_chunks": service.dense.store.count(),
        "modes": modes,
        "note": "all modes measured through the hybrid service; lexical "
                "pool = 20 candidates, dense pool = 20, rrf_k = 60",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"[OK] comparison written to {out}")
