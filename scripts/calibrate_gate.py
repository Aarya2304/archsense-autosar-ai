#!/usr/bin/env python
"""
Calibrate the M3.11 evidence gate (run once; documented in D-019).

    python scripts/calibrate_gate.py

Sweeps the refusal thresholds over the 30 answerable + 4 unanswerable GT
questions and writes data/evaluation/gate_calibration.json (git-ignored).
This is a one-time methodology artifact, not a tuning loop: thresholds are
chosen from the grid, recorded in docs/PROJECT_DECISIONS.md, and not
re-tuned against the same questions afterwards.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import EVAL_DIR  # noqa: E402
from backend.rag.embedder import get_embedder  # noqa: E402
from backend.rag.gate import build_gate_questions, evaluate_gate  # noqa: E402
from backend.rag.hybrid import get_hybrid_service  # noqa: E402
from backend.rag.vector_store import get_vector_store  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=None,
                    help="Embedding model (default: config EMBEDDING_MODEL)")
    ap.add_argument("--out", default=str(EVAL_DIR / "gate_calibration.json"))
    args = ap.parse_args()

    service = get_hybrid_service(embedder=get_embedder(args.model),
                                 store=get_vector_store())
    questions = build_gate_questions()
    n_unans = sum(1 for q in questions if q["unanswerable"])
    print(f"{len(questions)} questions ({len(questions) - n_unans} answerable"
          f" + {n_unans} unanswerable)")

    report = evaluate_gate(questions, service)
    chosen = [r for r in report["grid"]
              if r["min_lexical_score"] == 0.25
              and r["min_query_coverage"] == 0.30
              and r["min_score"] == 0.10]
    report["chosen"] = chosen[0] if chosen else None

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"chosen thresholds (D-019): {report['chosen']}")
    print(f"[OK] calibration grid written to {out}")
