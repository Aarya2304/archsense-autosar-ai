# PROJECT_DECISIONS

**Project:** ArchSense — AUTOSAR HLD Document Analysis Assistant (Tata Pulse Case Study 1 pilot)
**Created:** 2026-09-16 · **Status:** M0 + M1 complete; M2 pending approval
**Rule:** Every major technical decision is recorded here with reason,
alternatives considered, and consequences. Superseded decisions are struck
through, not deleted.

---

## D-001 — Project identity & product framing
- **Decision:** Name the tool **ArchSense**; frame it as an engineering
  workbench (Dashboard / Document Workspace / Architecture Explorer /
  Copilot / Findings / Revision Compare / Export), not a chat-with-PDF app.
- **Reason:** The case study (PDF pp.4–7) demands structured extraction,
  dependency mapping, inconsistency checks and human review — far beyond Q&A.
- **Alternatives:** Generic RAG demo; HARA/TARA variants (case studies 2–3).
- **Consequences:** Wider scope than a chatbot, mitigated by the MVP cut in
  the approved plan; 7 product areas map 1:1 to Streamlit pages in M8.

## D-002 — Synthetic corpus is the canonical dataset
- **Decision:** Build a fully synthetic AUTOSAR-style HLD corpus (v1.0.0 +
  v1.1.0, "Adaptive Body Controller") with ground truth generated from the
  same source-of-truth model that renders the PDFs.
- **Reason:** No authorized real HLD is available; synthetic data gives
  mechanical evaluation (extraction P/R, diff accuracy, refusal checks) and
  avoids any confidentiality risk. User confirmed this decision.
- **Alternatives:** Using a real sanitized HLD (unavailable); public
  AUTOSAR standard excerpts (licensing + no ground truth).
- **Consequences:** External documents remain a non-goal for evaluation; a
  real doc can be added later as an *additional* ingestion smoke test only.

## D-003 — Source-of-truth architecture (model → renderer → ground truth)
- **Decision:** One Python model (`backend/dataset/model.py`) drives both
  the ReportLab PDF renderer and the ground-truth builder.
- **Reason:** Ground truth can never drift from document content; regenerating
  both is a single command (`scripts/generate_dataset.py`).
- **Alternatives:** Hand-writing PDFs and ground truth separately (drift
  guaranteed); LLM-generated sample docs (unverifiable ground truth).
- **Consequences:** Evaluation numbers are trustworthy by construction.

## D-004 — Defect catalogue (8 planted defects, D1–D8)
- **Decision:** v2 applies 8 mechanical transforms: removed dependency
  (DEP-19), stale rename reference (IF-12 cell), conflicting provider
  (IF-13), orphan component (C-05), dropped-but-referenced signal (SG-015),
  prose contradiction (DEP-05 note), port-count prose/table mismatch (IF-04),
  new consumer of changed interface (IF-03 + C-04).
- **Reason:** Gives M6 (analysis) and M7 (diff) a known target set with gold
  pages; severities assigned by architectural impact.
- **Alternatives:** Random mutations (untraceable); fewer defects (weaker
  evaluation).
- **Consequences:** D4 uses a *minimal* transform — C-05 is removed from
  consumer lists, and only interfaces left providerless/consumerless are
  removed (IF-08, IF-09). This keeps the expected diff small and auditable
  (2 removed interfaces, 4 removed signals, 20 modified+unchanged interfaces).

## D-005 — Authoritative page index measured from rendered PDF
- **Decision:** The section→page ground-truth index is measured from the
  *rendered PDF* using the same heading rules as ingestion
  (`render_pdf.index_from_rendered_pdf`); ReportLab SectionMarkers are a
  fallback only.
- **Reason:** Layout flowables can bind to the preceding page at frame
  boundaries; measuring the artifact eliminates this entire bug class.
  Since both GT and ingestion use identical detection rules, M0/M1
  alignment is exact by construction (0 mismatches, 47 tests green).
- **Alternatives:** KeepTogether markers (still off-by-one in edge cases);
  manual page correction (drift risk).
- **Consequences:** Gold pages are trustworthy for citation evaluation in
  M3+; heading detection must stay conservative (uppercase-first rule).

## D-006 — MVP stack (per approved plan)
- **Decision:** Streamlit UI → service layer → domain modules; ChromaDB +
  BGE-family embeddings (benchmarked in M2); SQLite + SQLAlchemy; NetworkX +
  pyvis; OpenRouter (GPT-OSS-class model) with Ollama fallback behind one
  provider interface; **no** FastAPI server, Docker, LangChain, Neo4j, or
  PostgreSQL in the MVP.
- **Reason:** PDF alignment (FastAPI "preferred" not required, Streamlit for
  pilot); 11-day deadline; single-process reliability for the demo.
- **Alternatives:** FastAPI + separate frontend (deployment risk, no demo
  value); LangChain (abstraction overhead vs ~150 lines of custom RAG);
  Neo4j (≤100-node graph).
- **Consequences:** Service layer keeps a future FastAPI wrapper mechanical;
  Dockerfile possible as optional last-day add-on.

## D-007 — OCR as guarded fallback hook, not a milestone
- **Decision:** `pdf_parser.ocr_page_lines` surfaces an explicit,
  actionable error when a page has no text layer; no Tesseract dependency
  in M1.
- **Reason:** The synthetic corpus is digital (0 OCR pages); the case study
  lists OCR as in-scope, so the hook + documented Tesseract upgrade path
  covers the requirement honestly.
- **Alternatives:** Bundling Tesseract now (Windows install friction, zero
  demo value for digital docs).
- **Consequences:** Scanned PDFs fail loudly and early with guidance, rather
  than silently producing empty chunks.

## D-008 — Ingestion output contract (page records + tables + sections)
- **Decision:** `IngestionResult` carries per-page cleaned lines, text,
  counts, table anchors, and a section map; serialized to
  `data/processed/*__processed.json` for M2 chunking.
- **Reason:** Page-awareness is the citation backbone; tables are kept as
  matrices + text-linearized form (M2 chunking) and row strings (M6
  prose-vs-table checks).
- **Alternatives:** Streaming extraction straight into chunks (loses
  section/page semantics); storing raw PyMuPDF dicts (bulky, brittle).
- **Consequences:** M2 chunker consumes only this JSON; no re-parsing.

## D-009 — Section-detection conservativeness
- **Decision:** Heading regex requires an uppercase-alphabetic title start;
  duplicates resolve to first occurrence.
- **Reason:** Prevents wrapped prose ("…Section 5 lists…") and table rows
  ("2026-08-14 R. Sharma …") from creating phantom sections.
- **Alternatives:** Font-size-based detection (pdfplumber font data was
  unstable across pdfminer versions in quick checks; regex+guards was
  more reliable here).
- **Consequences:** Section map matches the rendered ground truth exactly.

## D-010 — Test-gate before milestones
- **Decision:** M0+M1 must pass the full test suite (47 tests) before M2
  begins; a 10-point end-to-end acceptance test gates the Sep 24 freeze.
- **Reason:** User-mandated incremental mode; dataset+ingestion bugs found
  late would poison every downstream milestone.
- **Alternatives:** Build everything then test once (untraceable failures).
- **Consequences:** Milestone reports to the user at each gate.

---

## Environment & commands (verified)

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe scripts/generate_dataset.py --force
./.venv/Scripts/python.exe scripts/process_sample_docs.py
./.venv/Scripts/python.exe -m pytest tests/        # 47 passed
```

## Open items / pending decisions

- **M2 embedder benchmark** (user requirement): BGE-M3 vs bge-small vs
  E5-small vs MiniLM on hit-rate@5 / MRR / RAM / latency on this laptop;
  winner recorded here with numbers.
- **M6 checks as quality goals:** start with the high-confidence core
  (undefined refs, dangling requires, duplicates, conflicting providers,
  orphans, unconsumed signals); add LLM-assisted checks only if meaningful.
- **LLM model choice for M3:** default `google/gemma-3-27b-it:free` via
  OpenRouter pending your API key; Ollama fallback config already stubbed
  in `.env.example`.
