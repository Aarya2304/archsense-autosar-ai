# ArchSense — AUTOSAR HLD Document Analysis Assistant

**AI-assisted architecture intelligence for automotive HLD documents.**
From unstructured HLDs to a cited, validated architecture knowledge base —
documents in, structured architecture knowledge, cited Q&A, consistency
findings, and revision impact out. Human review stays in the loop.

> Tata Pulse / Tata Technologies Case Study 1 pilot · fully synthetic demo
> corpus · work in progress toward the 2026-09-26 milestone.

## What it does (approved MVP scope)

1. **Ingest** AUTOSAR-style HLD PDFs page-aware: text, tables, headings,
   metadata (OCR fallback hook for scanned pages).
2. **Index** content into a local vector DB (ChromaDB) with section/page
   metadata for citation-accurate retrieval.
3. **Answer** engineering questions with strict, mechanically validated
   citations — and explicit refusal when evidence is insufficient.
4. **Extract** structured architecture knowledge (components, interfaces,
   ports, signals, dependencies, flows) via a deterministic + LLM hybrid.
5. **Visualize** the architecture graph with citations on relationships.
6. **Analyze** consistency/completeness with deterministic + LLM-assisted
   checks; findings go through human review (accept/reject/needs-discussion).
7. **Compare** HLD revisions (added/removed/modified + impact).
8. **Export** structured reports (JSON/CSV/Markdown) with audit trail.

## Status

| Milestone | Scope | Status |
|---|---|---|
| M0 | Synthetic dataset + ground truth | ✅ complete |
| M1 | Page-aware PDF ingestion | ✅ complete |
| M2 | Chunking, embedding benchmark, ChromaDB | ⏳ next |
| M3–M8 | RAG → extraction → graph → analysis → diff → polish | planned |

See `docs/IMPLEMENTATION_STATUS.md` for detail and
`docs/PROJECT_DECISIONS.md` for every major design decision.

## Quickstart

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
.venv/Scripts/python scripts/generate_dataset.py          # build synthetic HLDs + ground truth
.venv/Scripts/python scripts/process_sample_docs.py       # ingest them
.venv/Scripts/python -m pytest tests/                     # 47 tests
```

## Repository layout

```
backend/
  dataset/       M0: source-of-truth model, PDF renderer, ground truth
  ingestion/     M1: parsing, cleaning, sections, tables, OCR hook, pipeline
  rag/           M2/M3: chunker, embedder, vector store, retriever, LLM
  extraction/    M4: deterministic + LLM structured extraction
  graph/         M5: NetworkX builder + pyvis rendering
  analysis/      M6: deterministic + LLM checks
  diff/          M7: revision comparator + impact
  storage/       SQLite schema, sessions, audit log
  services/      application layer (UI-agnostic business logic)
app/             Streamlit UI (M8)
scripts/         dataset generation, ingestion, evaluation, acceptance run
tests/           pytest suite (47 tests green at M1)
docs/            decisions, status, architecture, evaluation, demo script
data/            generated artifacts (gitignored except fixtures)
```

## Governance principles (from the case study)

- Outputs grounded in approved source documents with page/section citations.
- Explicit **insufficient-evidence** refusal instead of guessing.
- AI-generated findings are *potential* issues; humans accept/reject.
- Append-only audit trail for every consequential action.
- Fully local storage; LLM access is via a single provider abstraction
  (OpenRouter primary, local Ollama fallback).

## License / data notice

The HLD corpus in `data/sample_docs/` is **synthetic** — it does not
represent any real vehicle program or proprietary architecture.
