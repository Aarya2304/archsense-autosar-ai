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
| M2 | Chunking, embedding benchmark, ChromaDB, retrieval + eval | ✅ complete |
| M3–M8 | RAG → extraction → graph → analysis → diff → polish | planned |

See `docs/IMPLEMENTATION_STATUS.md` for detail and
`docs/PROJECT_DECISIONS.md` for every major design decision.

## Quickstart

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
.venv/Scripts/python scripts/generate_dataset.py          # build synthetic HLDs + ground truth
.venv/Scripts/python scripts/process_sample_docs.py       # ingest them (M1)
.venv/Scripts/python scripts/build_vector_index.py        # chunks -> embeddings -> ChromaDB (M2)
.venv/Scripts/python scripts/evaluate_retrieval.py        # page_hit@K / MRR vs ground truth
.venv/Scripts/python -m pytest tests/                     # 104 tests
```

### Try retrieval (no LLM needed)

```bash
.venv/Scripts/python scripts/retrieve_demo.py "Which component provides VehicleSpeed?"
.venv/Scripts/python scripts/retrieve_demo.py "door signals" --top-k 5 --version 1.0.0
.venv/Scripts/python scripts/build_vector_index.py --model hashing   # zero-download mode
```

## M2 retrieval architecture

```
PDF ──(M1 ingestion)──> data/processed/*__processed.json
        │ page-aware lines + tables + section map (D-008)
        ▼
chunker.py ── deterministic section-aware chunks (D-011)
        │   prose: paragraph split -> ~500-token packs, ~75-token overlap
        │   tables: standalone linearized chunks (anchor-line attribution, D-012)
        │   provenance: doc, version, sha256, section, pages, seq, type
        ▼
embedder.py ── EmbeddingProvider (D-013)
        │     default: all-MiniLM-L6-v2 (benchmark D-014); hashing for tests
        ▼
vector_store.py ── VectorStore protocol -> ChromaDB (cosine, data/vectors/)
        │          deterministic chunk IDs -> re-index = upsert, no duplicates
        ▼
retriever.py ── retrieve(query, top_k, filters)
               filters: document_name / version / section_no / chunk_type /
               sha256 (+ page-range post-filter)
```

**Embedding benchmark (D-014, this corpus, CPU):** all-MiniLM-L6-v2 0.800
page-hit@5 at 159 texts/s · bge-small-en 0.800 @ 41 t/s · e5-small-v2 0.800
@ 49 t/s · bge-m3 0.867 @ 3 t/s · lexical hashing baseline 0.900 @ 3758 t/s.
MiniLM selected (tied quality, 3–4× faster); M3 adds hybrid lexical+dense
fusion because the QA vocabulary is lexical-heavy. Full methodology and
numbers: `docs/PROJECT_DECISIONS.md` D-014,
`data/evaluation/embedding_benchmark.json` (git-ignored).

## Repository layout

```
backend/
  dataset/       M0: source-of-truth model, PDF renderer, ground truth
  ingestion/     M1: parsing, cleaning, sections, tables, OCR hook, pipeline
  rag/           M2: chunker, embedder, benchmark, vector store, retriever,
                 indexing, evaluation  (M3 adds: llm/, citation validation)
  extraction/    M4: deterministic + LLM structured extraction
  graph/         M5: NetworkX builder + pyvis rendering
  analysis/      M6: deterministic + LLM checks
  diff/          M7: revision comparator + impact
  storage/       SQLite schema, sessions, audit log
  services/      application layer (UI-agnostic business logic)
app/             Streamlit UI (M8)
scripts/         dataset generation, ingestion, indexing, evaluation, demo
tests/           pytest suite (104 tests green at M2; opt-in model tests)
docs/            decisions, status, architecture, evaluation, demo script
data/            generated artifacts (gitignored: processed/, vectors/,
                 evaluation/, db/)
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
