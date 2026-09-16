# IMPLEMENTATION_STATUS

**Updated:** 2026-09-16 (M0 + M1 complete)
**Deadline:** 2026-09-26 · **Gate:** M2 starts only after user approval of this report.

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

### Infrastructure
- [x] Repository scaffold (backend / scripts / tests / docs / data layout)
- [x] SQLite schema: 13 tables with FKs, unique constraints, indexes
      (`backend/storage/models.py`)
- [x] Append-only audit log with failure isolation
- [x] `pytest.ini`, `conftest.py`, `.env.example`, `.gitignore`
- [x] `docs/PROJECT_DECISIONS.md` (10 decisions logged)

## Currently implementing

- *(none — M1 complete; stopped at the milestone gate as agreed)*

## Next up (requires approval)
- M2: chunker (section-aware, ~500 tokens, 15% overlap) + embedding-model
  benchmark (BGE-M3 vs bge-small-en vs E5-small vs MiniLM) + ChromaDB
  persistence + upload/process UI

## Test status

```
47 passed (13.0s)

tests/test_dataset_integrity.py  21 passed
tests/test_ingestion.py          18 passed
tests/test_storage.py             8 passed
```

Verification commands run this milestone:

```
scripts/generate_dataset.py --force   -> v1: 18 pages, v2: 17 pages
scripts/process_sample_docs.py        -> 51/44 tables, 93/84 sections
python -m pytest tests/               -> 47 passed
section-map GT alignment              -> 0 mismatches (both versions)
```

## Known bugs

- *(none open)* — 2 found and fixed during M1 (see D-004 note for the D4
  transform fix; hyphen-join off-by-one in `cleaning.py`).

## Blockers

- *(none)*

## Remaining work (milestone view)

| Milestone | Scope | Planned | Status |
|---|---|---|---|
| M2 | Chunker, embedding benchmark, ChromaDB, upload UI | Sep 17–18 | pending approval |
| M3 | RAG + mechanical citation validator + refusal | Sep 19 | — |
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
- Everything regenerates from scratch in <60 s via two commands.
