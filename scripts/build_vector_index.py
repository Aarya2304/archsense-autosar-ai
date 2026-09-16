#!/usr/bin/env python
"""
Build (or rebuild) the ChromaDB vector index from the sample HLDs (M2.5).

    python scripts/build_vector_index.py                # index both PDFs
    python scripts/build_vector_index.py --rebuild      # wipe collection first
    python scripts/build_vector_index.py --model hashing  # no downloads

Uses the configured default embedding model (backend.config.EMBEDDING_MODEL)
unless --model is given. M1 ingestion records are reused when present and
regenerated when missing. No LLM required.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import SAMPLE_DOCS_DIR, VECTORS_DIR  # noqa: E402
from backend.rag.embedder import get_embedder  # noqa: E402
from backend.rag.indexing import index_pdf  # noqa: E402
from backend.rag.vector_store import get_vector_store  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the ArchSense vector index")
    ap.add_argument("--rebuild", action="store_true",
                    help="Reset the collection before indexing")
    ap.add_argument("--model", default=None,
                    help="Embedding model name (default: config EMBEDDING_MODEL)")
    ap.add_argument("--docs-dir", default=str(SAMPLE_DOCS_DIR))
    ap.add_argument("--summary-out", default=str(
        VECTORS_DIR / "index_summary.json"))
    args = ap.parse_args()

    embedder = get_embedder(args.model)
    store = get_vector_store()
    print(f"embedder: {embedder.name} (dim={embedder.dimension})")
    print(f"store:    {store.name}")

    summaries = []
    for pdf in sorted(Path(args.docs_dir).glob("*.pdf")):
        chunks, summary = index_pdf(pdf, embedder, store,
                                    rebuild=args.rebuild)
        args.rebuild = False  # only the first document wipes the collection
        summaries.append(summary.to_dict())
        print(f"[OK] {pdf.name}: {summary.n_chunks} chunks "
              f"({summary.n_prose} prose / {summary.n_tables} tables) "
              f"-> collection size {summary.collection_size}")

    out = Path(args.summary_out)
    out.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(f"[OK] summary written to {out}")
