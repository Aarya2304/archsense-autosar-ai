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
| M3 | Hybrid retrieval + cited RAG copilot + refusal gate | ✅ complete |
| M4–M8 | Extraction → graph → analysis → diff → polish | planned |

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
.venv/Scripts/python -m pytest tests/                     # 195 tests
```

### Ask the copilot (M3, offline by default)

```bash
.venv/Scripts/python scripts/ask_copilot.py "Which component provides the VehicleSpeed signal?"
.venv/Scripts/python scripts/ask_copilot.py "What is the brake pressure of the front axle?"   # -> INSUFFICIENT EVIDENCE
.venv/Scripts/python scripts/ask_copilot.py "..." --json                 # full structured record
.venv/Scripts/python scripts/ask_copilot.py "..." --provider openrouter  # needs OPENROUTER_API_KEY in .env
```

The pipeline: **hybrid retrieval** (BM25 + dense fused with RRF) →
**evidence gate** (mechanical refusal when the question has no terminological
anchor in the corpus) → **grounded context** (rank-ordered evidence blocks) →
**LLM** (structured JSON, temperature 0) → **mechanical citation validation**
(evidence IDs resolved against trusted chunk metadata; fabricated IDs
rejected; quotes extracted, never LLM-written).

### Retrieval comparison + gate calibration

```bash
.venv/Scripts/python scripts/compare_retrieval_modes.py   # lexical vs dense vs hybrid
.venv/Scripts/python scripts/calibrate_gate.py            # refusal-threshold grid (one-shot)
.venv/Scripts/python scripts/retrieve_demo.py "door signals" --top-k 5 --version 1.0.0
.venv/Scripts/python scripts/build_vector_index.py --model hashing   # zero-download mode
```

## M3 RAG architecture

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
lexical.py + hybrid.py ── BM25 lexical mirror + dense search (D-016)
        │   RRF fusion: RRF(d) = Σ 1/(rrf_k + rank(d)), rrf_k=60
        │   modes: hybrid (default) | dense | lexical
        ▼
gate.py ── evidence gate (M3.11, D-019)
        │   lexical-hit floor + IDF query coverage + agreement
        │   refuse -> INSUFFICIENT EVIDENCE (no LLM call)
        ▼
context.py ── grounded evidence blocks [EVIDENCE E1..En] + rules (M3.7)
        ▼
llm/ ── LLMProvider: mock (default) | openrouter | ollama (D-017)
        │   structured JSON: {answer, evidence_ids, insufficient_evidence}
        ▼
citations.py ── mechanical validation (M3.8–M3.10, D-018)
        │   resolve [En] against trusted chunks; unknown IDs rejected;
        │   quotes extracted mechanically; metadata never from the LLM
        ▼
copilot.py ── CopilotAnswer: answered | insufficient_evidence |
             provider_failure | validation_failure
```

**Retrieval comparison (D-020, 30 GT questions, MiniLM):** hybrid beats
dense-only on every metric (page_hit@5 **0.867** vs 0.800; section_hit@5
**0.833** vs 0.700; MRR@5 **0.709** vs 0.658) and beats lexical-only at
K=5 (lexical keeps section_hit@1 and the latency crown at 2.8 ms).
**Embedding benchmark (D-014):** all-MiniLM-L6-v2 0.800 page-hit@5 at
159 texts/s · bge-small-en 0.800 @ 41 t/s · e5-small-v2 0.800 @ 49 t/s ·
bge-m3 0.867 @ 3 t/s · lexical hashing baseline 0.900 @ 3758 t/s.
Full methodology: `docs/PROJECT_DECISIONS.md` D-014/D-016/D-019/D-020;
artifacts in `data/evaluation/` (git-ignored).

## Repository layout

```
backend/
  dataset/       M0: source-of-truth model, PDF renderer, ground truth
  ingestion/     M1: parsing, cleaning, sections, tables, OCR hook, pipeline
  rag/           M2: chunker, embedder, benchmark, vector store, retriever,
                 indexing, evaluation
                 M3: lexical (BM25), hybrid (RRF), gate, context, citations,
                 copilot, llm/ (mock | openrouter | ollama)
  extraction/    M4: deterministic + LLM structured extraction
  graph/         M5: NetworkX builder + pyvis rendering
  analysis/      M6: deterministic + LLM checks
  diff/          M7: revision comparator + impact
  storage/       SQLite schema, sessions, audit log
  services/      application layer (UI-agnostic business logic)
app/             Streamlit UI (M8)
scripts/         dataset generation, ingestion, indexing, evaluation,
                 copilot CLI, gate calibration
tests/           pytest suite (195 tests green at M3; opt-in model tests)
docs/            decisions, status, architecture, evaluation, demo script
data/            generated artifacts (gitignored: processed/, vectors/,
                 evaluation/, db/)
```

## Governance principles (from the case study)

- Outputs grounded in approved source documents with page/section citations.
- Explicit **insufficient-evidence** refusal instead of guessing: a
  mechanical pre-generation gate refuses questions with no terminological
  anchor in the corpus; the LLM's structured refusal flag is the second
  layer; validation rejects uncited answers.
- Citations are mechanically validated: the model references evidence IDs
  only; document/version/section/page metadata and quotes come from stored
  chunks, never from model output.
- AI-generated findings are *potential* issues; humans accept/reject.
- Append-only audit trail for every consequential action.
- Fully local storage; LLM access is via a single provider abstraction
  (OpenRouter primary, local Ollama fallback, deterministic mock offline).

## License / data notice

The HLD corpus in `data/sample_docs/` is **synthetic** — it does not
represent any real vehicle program or proprietary architecture.
