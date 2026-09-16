#!/usr/bin/env python
"""
Generate the synthetic HLD dataset (M0).

Renders data/sample_docs/ABC_HLD_v1.0.0.pdf and ABC_HLD_v1.1.0.pdf and writes
data/ground_truth/ground_truth.json. Run from the repo root:

    python scripts/generate_dataset.py [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.dataset.ground_truth import (V1_PDF_PATH, V2_PDF_PATH,
                                          build_all)  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic HLD dataset")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate even if artifacts exist")
    args = parser.parse_args()

    gt = build_all(force=args.force)
    print(f"[OK] {V1_PDF_PATH}")
    print(f"[OK] {V2_PDF_PATH}")
    print(f"[OK] ground_truth.json (qa_pairs={len(gt['qa_pairs'])}, "
          f"expected_findings={len(gt['expected_findings'])}, "
          f"v1_components={len(gt['entities']['v1']['components'])}, "
          f"v2_components={len(gt['entities']['v2']['components'])})")
