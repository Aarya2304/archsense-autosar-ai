#!/usr/bin/env python
"""
Run the deterministic retrieval evaluation (M2.6).

    python scripts/evaluate_retrieval.py [--top-k 5] [--model hashing]

Scores the retrieval layer against the M0 ground-truth QA pairs and writes
data/evaluation/retrieval_eval.json (git-ignored). Deterministic per
(model, corpus): no LLM involved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import EVAL_DIR  # noqa: E402
from backend.rag.evaluation import evaluate_retrieval  # noqa: E402
from backend.rag.embedder import get_embedder  # noqa: E402
from backend.rag.retriever import RetrievalService  # noqa: E402
from backend.rag.vector_store import get_vector_store  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Evaluate retrieval quality")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--model", default=None,
                    help="Embedding model (default: config EMBEDDING_MODEL)")
    ap.add_argument("--out", default=str(
        EVAL_DIR / "retrieval_eval.json"))
    args = ap.parse_args()

    service = RetrievalService(embedder=get_embedder(args.model),
                               store=get_vector_store())
    report = evaluate_retrieval(service, top_k=args.top_k)
    print(f"top_k={args.top_k}  {report.summary_line()}")

    out = Path(args.out)
    report.save(out)
    print(f"[OK] per-question detail written to {out}")
