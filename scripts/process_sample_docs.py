#!/usr/bin/env python
"""
Run page-aware ingestion (M1) over the generated sample HLDs.

    python scripts/process_sample_docs.py [--out data/processed]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import PROCESSED_DIR, SAMPLE_DOCS_DIR  # noqa: E402
from backend.ingestion.pipeline import process_document  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(PROCESSED_DIR))
    ap.add_argument("--docs-dir", default=str(SAMPLE_DOCS_DIR))
    args = ap.parse_args()

    out_dir = Path(args.out)
    for pdf in sorted(Path(args.docs_dir).glob("*.pdf")):
        out = process_document(pdf, out_dir=out_dir)
        print(f"[OK] {pdf.name} -> {out}")
