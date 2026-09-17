#!/usr/bin/env python
"""
Run structured entity/fact extraction (M4) over the sample HLD corpus.

    python scripts/extract_entities.py                      # both versions, deterministic
    python scripts/extract_entities.py --version 1.0.0      # one version
    python scripts/extract_entities.py --llm                # + mock LLM pass
    python scripts/extract_entities.py --provider openrouter --llm   # real LLM (key needed)
    python scripts/extract_entities.py --reset              # clear registry before run
    python scripts/extract_entities.py --json               # machine-readable output

The deterministic pass needs no LLM and no network. The LLM pass uses the
M3 provider abstraction (mock by default = offline; openrouter/ollama work
once configured in .env). Extraction output is persisted into the existing
SQLite storage layer (typed entity tables + extraction_facts + audit).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import PROCESSED_DIR  # noqa: E402
from backend.extraction.llm import MockExtractionProvider  # noqa: E402
from backend.extraction.service import ExtractionService  # noqa: E402
from backend.rag.chunker import chunk_processed_json  # noqa: E402

_CORPUS = [
    ("1.0.0", PROCESSED_DIR / "ABC_HLD_v1.0.0__processed.json"),
    ("1.1.0", PROCESSED_DIR / "ABC_HLD_v1.1.0__processed.json"),
]


def _provider(name: str):
    if name == "mock":
        return MockExtractionProvider()
    from backend.rag.llm.factory import get_llm_provider
    return get_llm_provider(name)


def _result_to_dict(res) -> dict:
    return {
        "status": res.status,
        "document_name": res.document_name,
        "version": res.version,
        "run_id": res.run_id,
        "n_unique_entities": len({e.key for e in res.entities}),
        "n_facts": len(res.facts),
        "issues": res.issues,
        "stats": res.stats,
        "timings_ms": res.timings_ms,
        "entities": [
            {"key": e.key, "name": e.name, "type": e.entity_type.value,
             "confidence": e.confidence, "extractor": e.extractor,
             "attributes": e.attributes,
             "pages": e.evidence.source.pages_csv if e.evidence.source else "",
             "chunk_id": (e.evidence.source.chunk_id
                          if e.evidence.source else "")}
            for e in res.entities],
        "facts": [
            {"subject": f.subject, "predicate": f.predicate.value,
             "object": f.object, "confidence": f.confidence,
             "extractor": f.extractor,
             "pages": f.evidence.source.pages_csv if f.evidence.source else "",
             "chunk_id": (f.evidence.source.chunk_id
                          if f.evidence.source else "")}
            for f in res.facts],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--document", default=None,
                    help="filename filter (e.g. ABC_HLD_v1.0.0.pdf)")
    ap.add_argument("--version", default=None,
                    help="version filter (1.0.0 or 1.1.0)")
    ap.add_argument("--llm", action="store_true",
                    help="also run the LLM extraction pass")
    ap.add_argument("--provider", default=None,
                    help="LLM provider for --llm (mock|openrouter|ollama)")
    ap.add_argument("--deterministic-only", action="store_true",
                    help="force deterministic-only (disables --llm)")
    ap.add_argument("--min-confidence", type=float, default=None,
                    help="reject candidates below this confidence")
    ap.add_argument("--reset", action="store_true",
                    help="clear stored extraction output for the version first")
    ap.add_argument("--no-persist", action="store_true",
                    help="do not write to the SQLite registry")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable JSON output")
    args = ap.parse_args()

    use_llm = args.llm and not args.deterministic_only
    provider = _provider(args.provider) if use_llm and args.provider else (
        MockExtractionProvider() if use_llm else None)
    svc = ExtractionService(
        llm_provider=provider, use_llm=use_llm,
        min_confidence=(args.min_confidence if args.min_confidence is not None
                        else 0.0))

    targets = [(v, p) for v, p in _CORPUS
               if (args.version is None or v == args.version)
               and (args.document is None
                    or p.name.startswith(Path(args.document).stem))]
    if not targets:
        print(f"No processed documents matched (looked in {PROCESSED_DIR}). "
              f"Run scripts/process_sample_docs.py first.", file=sys.stderr)
        return 1

    results = []
    for ver, pj in targets:
        chunks = chunk_processed_json(pj)[:400]
        res = svc.extract_from_chunks(
            chunks, document_name=pj.name.replace("__processed.json", ".pdf"),
            version=ver, persist=not args.no_persist, reset=args.reset)
        results.append(res)
        if not args.json:
            print("=" * 60)
            print(res.summary())
            if res.issues:
                kinds = {}
                for i in res.issues:
                    kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
                print(f"Issue kinds: {kinds}")

    if args.json:
        print(json.dumps([_result_to_dict(r) for r in results], indent=2))
    else:
        print("\nRegistry: "
              + ("not written (--no-persist)"
                 if args.no_persist else
                 "updated (data/db/archsense.db; see scripts/query_entities.py)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
