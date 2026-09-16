# IMPLEMENTATION_STATUS

**Updated:** 2026-09-16 (M0 + M1 + M2 complete)
**Deadline:** 2026-09-26 · **Gate:** M3 starts after user review of this M2 report.

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
- [x] OCR fallback hook (explicit error on scan-only pages; Tesseract =
      documented future scope)
- [x] Pipeline orchestration + JSON persistence
      (`data/processed/*__processed.json`)
- [x] CLI: `scripts/process_sample_docs.py`

### M2 — RAG foundation (this milestone)
- [x] **Chunking (M2.1)** `backend/rag/chunker.py` + `models.py`:
      deterministic section-aware chunking; provenance on every chunk
      (document, version, sha256, section, title, page span, `pages_csv`,
      seq, type, token count); prose split on paragraph boundaries, packed
      to ~500 tokens with ~75-token overlap; tables emitted as standalone
      coherent chunks via anchor-line section attribution (D-011, D-012)
- [x] **Embedders (M2.2)** `backend/rag/embedder.py`:
      `EmbeddingProvider` protocol; deterministic 512-dim hashing embedder
      (tests/offline); `SentenceTransformerEmbedder` with E5 query/passage
      prefix support; factory + timing/size helpers
- [x] **Benchmark (M2.2)** `backend/rag/benchmark.py` +
      `scripts/benchmark_embeddings.py`: 5 candidates measured on identical
      corpus/questions; machine-readable result at
      `data/evaluation/embedding_benchmark.json`; decision D-014
      (**all-MiniLM-L6-v2** selected)
- [x] **Vector store (M2.3)** `backend/rag/vector_store.py`:
      `VectorStore` protocol + `ChromaVectorStore` (cosine space, persisted
      under `data/vectors/chroma/`); upsert dedupe via deterministic IDs;
      metadata filters; reset; persistence across processes verified
- [x] **Retrieval (M2.4)** `backend/rag/retriever.py`: `RetrievalService`
      with `retrieve(query, top_k, filters)`; equality filters
      (document_name/version/section_no/chunk_type/sha256) + client-side
      page-range post-filter; unknown filter keys rejected
- [x] **Indexing pipeline (M2.5)** `backend/rag/indexing.py` +
      `scripts/build_vector_index.py`: processed JSON → chunks → embeddings
      → ChromaDB; reuses M1 ingestion records; `--rebuild`; no LLM involved
- [x] **Evaluation (M2.6)** `backend/rag/evaluation.py` +
      `scripts/evaluate_retrieval.py`: page/section hit@K, MRR_page,
      latency; deterministic; per-question detail saved
- [x] **Demo (M2.9)** `scripts/retrieve_demo.py`: query → ranked chunks
      with rank/score/document/version/section/pages/chunk-ID/text
- [x] **Tests (M2.7)** 57 new tests (chunker 17, embedder 9, store 11,
      retrieval/indexing/eval 20); real-model tests marked `model` and
      deselected by default (`-m "not model"` in pytest.ini)

## Currently implementing

- *(nothing — M2 complete, stopped at the milestone gate)*

## Next up (requires approval)
- M3: cited RAG copilot — hybrid lexical+dense retrieval (RRF; D-014
  finding), OpenRouter/Ollama LLM provider, mechanical citation validator
  (chunk-ID resolution + quote extraction), INSUFFICIENT-EVIDENCE refusal,
  unanswerable-question calibration against the 4 GT negatives

## Test status

```
104 passed, 1 deselected (12.9s)      # default: fast + deterministic suite
1 passed (opt-in, real MiniLM)        # pytest -m model (HF download)

tests/test_dataset_integrity.py   21 passed
tests/test_ingestion.py           18 passed
tests/test_storage.py              8 passed
tests/test_rag_chunker.py         17 passed
tests/test_rag_embedder.py         8 passed (+1 opt-in model test)
tests/test_rag_vector_store.py    11 passed
tests/test_rag_retrieval.py       20 passed
```

Verification commands run this milestone (all outputs above in transcript):

```
scripts/build_vector_index.py --rebuild
    -> 145 + 134 = 279 chunks indexed; re-run without --rebuild
       still 279 (no duplicates)
scripts/evaluate_retrieval.py
    -> page_hit@5=0.800  section_hit@5=0.700  MRR=0.658  median 11 ms (n=30)
scripts/benchmark_embeddings.py (4 real models + hashing baseline)
    -> data/evaluation/embedding_benchmark.json (git-ignored)
python -m pytest tests/            -> 104 passed, 1 deselected
```

## Known bugs

- *(none open)* — M2 fixes: table→section misattribution (D-012),
  deprecated `get_sentence_embedding_dimension` (ST ≥ 5 rename),
  invalid `quiet` kwarg, E5 prefix plumbing via `embed_query`.

## Blockers

- *(none)* — benchmark models cached locally in HF cache (~2.7 GB total,
  outside the repo; nothing committed).

## Remaining work (milestone view)

| Milestone | Scope | Planned | Status |
|---|---|---|---|
| M2 | Chunker, benchmark, ChromaDB, retrieval, eval | Sep 17–18 | ✅ complete |
| M3 | Hybrid RAG + citation validator + refusal | Sep 19 | next |
| M4 | Hybrid structured extraction + registry | Sep 20 | — |
| M5 | Graph explorer + HLD_v2 finalization | Sep 21 | — |
| M6 | Deterministic checks + findings review UI | Sep 22 | — |
| M7 | Revision compare + impact | Sep 23 | — |
| M8 | Polish, export, evaluation harness, acceptance test | Sep 24 | — |
| — | Docs, traceability matrix, Drive submission | Sep 25 | — |
| — | Demo dry-runs, backup | Sep 26 | — |

## Notes for evaluators / demo

- The corpus is fully synthetic (banner on every page) — no real program
  data; architecture realism was prioritized (AUTOSAR naming, BSW stack,
  S-R/C-S interfaces, NvM/EcuM/CanSm semantics).
- Everything regenerates from scratch in <60 s via the three dataset
  commands; vector indexing adds ~40 s (MiniLM on CPU).
- Honest evaluation numbers are a feature: dense-only retrieval is *worse*
  than lexical on this QA set (D-014) — the demo will show hybrid
  retrieval in M3, not hide the finding.
