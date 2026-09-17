#!/usr/bin/env python
"""
Ask the ArchSense copilot a question (M3.13).

    python scripts/ask_copilot.py "Which component provides VehicleSpeed?"
    python scripts/ask_copilot.py "What is stored in NVRAM?" --version 1.0.0
    python scripts/ask_copilot.py "..." --provider openrouter   (needs key)
    python scripts/ask_copilot.py "..." --json                  (full record)

Pipeline: hybrid retrieval -> evidence gate -> grounded context -> LLM ->
mechanical citation validation -> grounded answer with trusted citations,
or a structured INSUFFICIENT EVIDENCE / failure state.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.rag.copilot import RAGCopilot  # noqa: E402
from backend.rag.embedder import get_embedder  # noqa: E402
from backend.rag.hybrid import get_hybrid_service  # noqa: E402
from backend.rag.llm.factory import get_llm_provider  # noqa: E402
from backend.rag.vector_store import get_vector_store  # noqa: E402


def _print_human(answer) -> None:
    print(f"Q: {answer.question}")
    print(f"status: {answer.status}")
    if answer.status == "answered":
        print(f"\nAnswer:\n{answer.answer}\n")
        print(answer.sources_block)
    elif answer.status == "insufficient_evidence":
        print("\nINSUFFICIENT EVIDENCE - no answer will be provided.")
        print(f"reason: {answer.detail}")
        if answer.gate.get("missing_terms"):
            print(f"terms not found in evidence: "
                  f"{', '.join(answer.gate['missing_terms'][:8])}")
    else:
        print(f"\n{answer.status.replace('_', ' ').upper()}: {answer.detail}")
    print(f"\n[retrieval: mode={answer.retrieval.get('mode')} "
          f"chunks={answer.retrieval.get('n_chunks')} | "
          f"llm: {answer.llm.get('provider')}/{answer.llm.get('model', '-')} | "
          f"total {answer.timings_ms.get('total_ms', 0):.0f} ms]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Ask the ArchSense copilot")
    ap.add_argument("question", help="Engineering question about the HLD")
    ap.add_argument("--provider", default=None,
                    help="LLM provider: mock (default), openrouter, ollama")
    ap.add_argument("--model", default=None,
                    help="Embedding model override (default: config)")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--version", default=None,
                    help="Filter to one document version, e.g. 1.0.0")
    ap.add_argument("--json", action="store_true",
                    help="Print the full structured result as JSON")
    args = ap.parse_args()

    filters = {"version": args.version} if args.version else None
    retriever = get_hybrid_service(embedder=get_embedder(args.model),
                                   store=get_vector_store())
    copilot = RAGCopilot(retriever=retriever,
                         llm=get_llm_provider(args.provider),
                         top_k=args.top_k)
    answer = copilot.ask(args.question, filters=filters)

    if args.json:
        print(json.dumps(answer.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_human(answer)
