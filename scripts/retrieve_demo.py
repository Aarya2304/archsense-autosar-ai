#!/usr/bin/env python
"""
Interactive retrieval demo (M2.9) -- NOT the AI copilot.

    python scripts/retrieve_demo.py "Which component provides VehicleSpeed?"
    python scripts/retrieve_demo.py "door signals" --top-k 5 --version 1.0.0

Prints rank, score, document, version, section, pages, chunk ID and the
retrieved text for the top-K chunks. No LLM is called.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.rag.embedder import get_embedder  # noqa: E402
from backend.rag.retriever import RetrievalService  # noqa: E402
from backend.rag.vector_store import get_vector_store  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Retrieve chunks for a query")
    ap.add_argument("query", help="Natural-language query")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--model", default=None,
                    help="Embedding model (default: config EMBEDDING_MODEL)")
    ap.add_argument("--version", default=None,
                    help="Filter by document version, e.g. 1.0.0")
    ap.add_argument("--chunk-type", default=None,
                    choices=["prose", "table"],
                    help="Filter by chunk type")
    args = ap.parse_args()

    filters = {}
    if args.version:
        filters["version"] = args.version
    if args.chunk_type:
        filters["chunk_type"] = args.chunk_type

    service = RetrievalService(embedder=get_embedder(args.model),
                               store=get_vector_store())
    result = service.retrieve(args.query, top_k=args.top_k,
                              filters=filters or None)

    print(f"query: {args.query!r}  | filters: {filters or '{}'}")
    if not result.chunks:
        print("(no hits)")
    for d in result.to_dicts():
        print(f"\n#{d['rank']}  score={d['score']:.4f}  "
              f"{d['document_name']}  v{d['version']}  "
              f"sec={d['section_no'] or '(front)'}  "
              f"pages={d['pages']}  {d['chunk_type']}")
        print(f"    chunk {d['chunk_id']}  ({d['token_count']} tok)")
        text = d["text"]
        preview = text if len(text) <= 600 else text[:600] + " ..."
        for line in preview.splitlines():
            print(f"    | {line}")
