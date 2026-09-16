#!/usr/bin/env python
"""
Benchmark candidate embedding models (M2.2).

    python scripts/benchmark_embeddings.py                 # all candidates
    python scripts/benchmark_embeddings.py --models hashing all-MiniLM-L6-v2

Downloads models on first use (HuggingFace cache); pass --no-download-safe
candidates via --models to limit scope. Writes
data/evaluation/embedding_benchmark.json (git-ignored). Deterministic given
the same corpus + model versions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import (EMBEDDING_MODEL_CANDIDATES,  # noqa: E402
                            EVAL_DIR)
from backend.rag.benchmark import run_benchmark  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Benchmark embedding models")
    ap.add_argument("--models", nargs="*", default=None,
                    help="Candidate model names (default: config candidates)")
    ap.add_argument("--max-chunks", type=int, default=None,
                    help="Cap corpus size for quick runs")
    ap.add_argument("--out", default=str(EVAL_DIR / "embedding_benchmark.json"))
    args = ap.parse_args()

    models = args.models or EMBEDDING_MODEL_CANDIDATES
    report = run_benchmark(models, output_path=Path(args.out),
                           max_chunks=args.max_chunks)

    print("\n=== summary (page_hit@5 / MRR_page / texts-per-second) ===")
    for m in report["models"]:
        m5 = m["metrics_by_k"].get("5", {})
        print(f"  {m['model']:<32} dim={m['dimension']:<4} "
              f"hit@5={m5.get('page_hit', 0):.3f}  "
              f"mrr={m5.get('mrr_page', 0):.3f}  "
              f"tps={m['texts_per_second']:.0f}"
              + (f"  ERROR={m['error']}" if m.get("error") else ""))

    # Record where the summary JSON landed (kept out of git by design).
    print(f"[OK] {json.dumps({'report': str(args.out)})}")
