# IMPLEMENTATION_STATUS

**Updated:** 2026-09-16 (M0 + M1 + M2 + M3 complete)
**Deadline:** 2026-09-26 · **Gate:** M4 starts after user review of this M3 report.

---

## Completed features

### M0 — Synthetic dataset (canonical corpus)
- [x] Source-of-truth model: 20 components, 25 interfaces, 34 signals,
      24 dependencies, 5 functional flows (`backend/dataset/model.py`)
- [x] Realistic ReportLab renderer: numbered sections, port/signal/dependency
      tables, revision history, cross-references, headers/footers
      (`backend/dataset/render_pdf.py`)
- [x] **HLD_v1.0.0.pdf** (18 pages) and **HLD_v1.1.0-draft.pdf** (17 pages)
      in `data/sample_docs/`
- [x] 8 planted defects (D1–D8) as mechanical v2 transforms
- [x] Ground truth JSON: entity registries (v1+v2), ports, mechanically
      computed expected diff, 8 gold findings with resolved pages,
      30 Q&A pairs with gold citations, 4 unanswerable questions
- [x] Authoritative page index measured from rendered PDF (D-005)

### M1 — Page-aware ingestion
- [x] SHA-256 + metadata extraction; encrypted-PDF guard
- [x] Per-page text extraction (physical line order)
- [x] Cleaning: headers/footers/page numbers/banner stripping,
      hyphen-join, whitespace normalization
- [x] Heading/section detection with conservative guards
- [x] pdfplumber table extraction with page anchors (51 tables in v1)
- [x] OCR fallback hook (explicit error on scan-only pages)
- [x] Pipeline orchestration + JSON persistence + CLI

### M2 — RAG foundation
- [x] Deterministic section-aware chunking with full provenance and
      content-addressed chunk IDs (D-011/D-012)
- [x] `EmbeddingProvider` abstraction; hashing embedder (offline) +
      sentence-transformers embedder (E5 prefix support)
- [x] Embedding benchmark (5 candidates) → **all-MiniLM-L6-v2** default (D-014)
- [x] `VectorStore` protocol + ChromaDB implementation (cosine, persisted,
      upsert-dedupe, reset, `get_all_chunks` for the lexical mirror)
- [x] `RetrievalService` (dense) with metadata filtering — unchanged in M3
- [x] Indexing pipeline `processed JSON → chunks → embeddings → ChromaDB`
- [x] Retrieval evaluation (page/section hit@K, MRR, latency) + demo CLI

### M3 — Cited RAG copilot (this milestone)
- [x] **Post-review hardening (2026-09-17)** — citation validator tightened:
      ANY unknown evidence ID (inline `[En]` or declared) now fails the whole
      response (`ok=False` → `validation_failure`, citations emptied) even
      when other citations are valid; regression tests added at the
      validator level (mixed valid+fabricated inline; fabricated declared
      ID) and at the copilot level (answer can never surface).
- [x] **Hybrid retrieval (M3.1/M3.2)** `backend/rag/lexical.py` +
      `backend/rag/hybrid.py`: deterministic in-memory Okapi BM25
      (k1=1.5, b=0.75) over a store-mirroring lexical index;
      Reciprocal Rank Fusion (`rrf_k=60`, rank-based, deterministic
      tie-breaks); fused hits carry `rrf_score`/`sources`/per-list ranks;
      `HybridRetrievalService` with hybrid (default) / dense / lexical
      modes; dense path delegates to the untouched M2 service (D-016)
- [x] **LLM providers (M3.4–M3.6)** `backend/rag/llm/`:
      `LLMProvider` protocol + `LLMResponse`; `MockLLMProvider`
      (deterministic offline, default), `OpenRouterProvider`
      (OpenAI-compatible, temp 0, retries on 429/5xx only, fail-fast
      config errors, redacted keys), `OllamaProvider` (local, optional);
      factory `get_llm_provider()` (D-017)
- [x] **Grounded context builder (M3.7)** `backend/rag/context.py`:
      rank-ordered evidence blocks (`[EVIDENCE E1]` + document/version/
      section/pages/type), grounding system prompt (answer only from
      evidence, cite IDs, refuse when insufficient, never invent IDs),
      deterministic construction
- [x] **Mechanical citation validation (M3.8–M3.10)**
      `backend/rag/citations.py`: structured-JSON parsing (fence/prose
      tolerant, explicit failure states), evidence-ID resolution against
      the trusted map, unknown-ID rejection, mechanically extracted quotes,
      deterministic citation rendering — the LLM never supplies
      document/version/section/page metadata (D-018)
- [x] **Evidence gate + refusal (M3.11)** `backend/rag/gate.py`: lexical-hit
      floor + IDF query-coverage of evidence + optional agreement;
      calibrated one-shot on 30 answerable + 4 unanswerable GT questions
      (D-019, `scripts/calibrate_gate.py`); two-layer defense with the
      LLM's structured refusal
- [x] **RAGCopilot (M3.12)** `backend/rag/copilot.py`: retrieve → gate →
      context → LLM → validate; four structured outcomes (answered /
      insufficient_evidence / provider_failure / validation_failure);
      dependency-injected retriever + provider; accepts the M2 dense
      service unchanged (backwards compatible)
- [x] **Evaluation (M3.3)** `scripts/compare_retrieval_modes.py`:
      lexical vs dense vs hybrid, K∈{1,3,5}, per-question detail (D-020);
      **hybrid > dense on all metrics**, hybrid > lexical at K=5
- [x] **CLI (M3.13)** `scripts/ask_copilot.py`: human + `--json` output,
      provider/model/version flags; offline by default (mock provider)
- [x] **Tests (M3.15)** 91 new tests across 5 files (hybrid 22, llm 23,
      context/citations 24, copilot/gate 22); all 104 pre-M3 tests still
      pass; no network/API keys required anywhere

## Currently implementing

- *(nothing — M3 complete, stopped at the milestone gate)*

## Next up (requires approval)
- M4: structured architecture extraction (components/interfaces/ports/
  signals/dependencies) — deterministic parsing + LLM structured output
  with the same mechanical-validation pattern (Pydantic models, entity
  registry, confidence + provenance on every entity)

## Test status

```
198 passed, 1 deselected (~15s)       # default: fast + deterministic suite
1 passed (opt-in, real MiniLM)        # pytest -m model (HF download)

tests/test_dataset_integrity.py   21 passed
tests/test_ingestion.py           18 passed
tests/test_storage.py              8 passed
tests/test_rag_chunker.py         17 passed
tests/test_rag_embedder.py         8 passed (+1 opt-in model test)
tests/test_rag_vector_store.py    11 passed
tests/test_rag_retrieval.py       20 passed
tests/test_rag_hybrid.py          22 passed   (M3)
tests/test_rag_llm.py             23 passed   (M3)
tests/test_rag_context_citations.py 26 passed (M3, incl. 2 hardening regressions)
tests/test_rag_copilot.py         23 passed   (M3, incl. 1 hardening regression)
```

## M3 evaluation results (actual runs, this corpus, MiniLM)

Retrieval modes (30 GT questions, `scripts/compare_retrieval_modes.py`):

| metric | lexical | dense (M2) | **hybrid (M3)** |
|---|---|---|---|
| page_hit@1 | **0.633** | 0.600 | 0.633 |
| section_hit@1 | **0.500** | 0.400 | 0.467 |
| page_hit@3 | 0.800 | 0.667 | 0.800 |
| section_hit@3 | 0.700 | 0.600 | **0.733** |
| page_hit@5 | 0.833 | 0.800 | **0.867** |
| section_hit@5 | 0.767 | 0.700 | **0.833** |
| MRR_page@5 | 0.701 | 0.658 | **0.709** |
| median latency | **2.8 ms** | 12.5 ms | 14.6 ms |

Honest read: hybrid improves the M2 dense baseline on **every** metric and
beats lexical-only at K=5; lexical-only retains section_hit@1 and is
fastest. Machine-readable: `data/evaluation/retrieval_modes_comparison.json`
(git-ignored).

Evidence gate calibration (one-shot grid, D-019,
`data/evaluation/gate_calibration.json`):

- Refused by gate mechanically: **QA-U1** (zero lexical match),
  **QA-U2** (query coverage 0.29 < 0.30)
- Pass the gate by design (topically adjacent): **QA-U3, QA-U4** → refused
  by the LLM structured-refusal layer in production; answered by the
  offline mock (documented mock limitation — no token signal separates
  them; measured and reported in D-019)
- Cost: **1 false refusal** (QA-21, coverage 0.22) of 30 answerable

## Known bugs

- *(none open)* — M3 fixes during development: mock block-splitter absorbed
  the `Question:` footer (every question trivially matched its own
  evidence); gate `best_score` compared raw BM25 against a bounded
  threshold; context/mock `[EVIDENCE E1]` contract mismatch; Windows
  console encoding (em-dash → ASCII in rendered citations).

## Blockers

- *(none)* — OpenRouter key not present in the environment, so the live
  provider smoke test (M3.16) has NOT been run; the provider
  implementation is verified via mocked HTTP tests and the offline mock
  path. Run `scripts/ask_copilot.py "<q>" --provider openrouter` once a
  key is configured in `.env`.

## Remaining work (milestone view)

| Milestone | Scope | Planned | Status |
|---|---|---|---|
| M3 | Hybrid RAG + citation validator + refusal | Sep 19 | ✅ complete |
| M4 | Hybrid structured extraction + registry | Sep 20 | next |
| M5 | Graph explorer + HLD_v2 finalization | Sep 21 | — |
| M6 | Deterministic checks + findings review UI | Sep 22 | — |
| M7 | Revision compare + impact | Sep 23 | — |
| M8 | Polish, export, evaluation harness, acceptance test | Sep 24 | — |
| — | Docs, traceability matrix, Drive submission | Sep 25 | — |
| — | Demo dry-runs, backup | Sep 26 | — |

## Notes for evaluators / demo

- The copilot's refusal story is demonstrable offline: brake-pressure
  questions are refused *before* any LLM call with a mechanical reason
  (`no strong lexical match ... question may be outside the corpus`).
- Citation provenance is mechanically enforced: the model can only
  reference evidence IDs; document/version/section/page/quote all come
  from stored chunk metadata — fabricated IDs are rejected by tests.
- Everything regenerates from scratch in <60 s via the three dataset
  commands; vector indexing adds ~40 s (MiniLM on CPU); hybrid retrieval
  adds ~2 ms over dense-only.
