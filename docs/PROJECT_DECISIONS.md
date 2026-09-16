# PROJECT_DECISIONS

**Project:** ArchSense — AUTOSAR HLD Document Analysis Assistant (Tata Pulse Case Study 1 pilot)
**Created:** 2026-09-16 · **Status:** M0 + M1 + M2 complete (M2 pending user review)
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

## D-011 — Deterministic section-aware chunking (M2.1)
- **Decision:** The chunker re-groups M1 page lines into per-section
  streams (heading lines start their own stream), splits prose on paragraph
  boundaries, packs to a ~500-token target (4 chars/token heuristic) with
  ~75-token overlap on continuation chunks, and emits every extracted table
  as a standalone linearized `table` chunk. Chunk IDs are sha1-based and
  content-addressed (`doc_sha|version|section|seq|text_hash`), so re-running
  on identical input is a no-op upsert, while edited content gets fresh IDs.
- **Reason:** Sections/pages are the citation backbone; arbitrary
  fixed-size splitting would shred tables and provenance. Determinism is a
  hard requirement for duplicate-free re-indexing and reproducible eval.
- **Alternatives:** LangChain RecursiveCharacterTextSplitter (section-
  blind, non-deterministic IDs); LLM-based semantic chunking (slow,
  non-deterministic, unnecessary for structured HLDs).
- **Consequences:** Over-long sections split at paragraph/sentence level;
  tiny sections stay whole; `pages_csv` metadata preserves multi-page chunk
  provenance for ChromaDB (scalar-metadata constraint).

## D-012 — Table→section attribution via anchor lines (M2.1)
- **Decision:** Each extracted table is attributed to the numbered heading
  in effect at the table's anchor line (first header cell, else first
  row's first cell) in the page's reading order, with the active section
  carried across pages; fallback = deepest section starting on or before
  the table's page (numeric tie-break).
- **Reason:** The naive rule used in the first chunker draft ("deepest
  section starting on this page, lexical tie-break highest") misattributed
  both page-2 tables to 1.5. Anchor-line assignment fixed attribution for
  revision history (1.3), terminology (1.5), component inventory (3.1) and
  the dependency overview table (6.1, which physically sits between the
  6.1 and 6.2 headings despite powering the 6.2.x detail sections).
- **Alternatives:** pdfplumber bbox-to-heading geometry (font data proved
  unstable in M1); leaving tables unattributed (breaks section filtering).
- **Consequences:** Table chunks are citable at section granularity, which
  M3 citations and M6 prose-vs-table checks depend on.

## D-013 — Provider/store abstraction, no framework lock-in (M2.3/M2.4)
- **Decision:** `EmbeddingProvider` (embed/embed_query) and `VectorStore`
  (upsert/count/query/reset) are small runtime-checkable protocols;
  ChromaDB sits behind `ChromaVectorStore` and sentence-transformers behind
  `SentenceTransformerEmbedder`. Application code never imports chromadb or
  sentence_transformers directly outside these two modules.
- **Reason:** M2 requirement (no tight ChromaDB coupling); keeps the
  benchmark able to swap stores, and unit tests run on a deterministic
  512-dim hashing embedder + fresh stores with zero downloads.
- **Alternatives:** LangChain VectorStore abstraction (drags the whole
  dependency into the MVP, against D-006); a concrete base class instead of
  a Protocol (heavier, no benefit for 2 implementations).
- **Consequences:** Replacing ChromaDB (or adding a lexical store for M3
  hybrid retrieval) is additive; `embed_query` exists so asymmetric models
  (E5 `query:`/`passage:` prefixes) are handled without leaking prefixes
  into the retriever.

## D-014 — Default embedding model: all-MiniLM-L6-v2 (benchmark-driven)
- **Decision:** `all-MiniLM-L6-v2` is the M2 default embedder, chosen from
  the benchmark in `data/evaluation/embedding_benchmark.json` (279 chunks,
  30 gold QA questions, K∈{1,3,5,10}, same corpus for every candidate).
- **Reason — measured on this corpus (CPU, this laptop):**

  | model | dim | size (f32) | hit@5 | MRR_page | texts/s |
  |---|---|---|---|---|---|
  | all-MiniLM-L6-v2 | 384 | 87 MB | 0.800 | 0.658 | 159 |
  | BAAI/bge-small-en-v1.5 | 384 | 127 MB | 0.800 | 0.681 | 41 |
  | intfloat/e5-small-v2 | 384 | 127 MB | 0.800 | 0.665 | 49 |
  | BAAI/bge-m3 | 1024 | 2166 MB | 0.867 | 0.729 | 3 |
  | hashing-512 (baseline) | 512 | — | 0.900 | 0.684 | 3758 |

  bge-m3 is the best *dense* model (+0.067 hit@5 vs the small tier) but is
  ~40–50× slower and ~17× larger, for marginal demo value. Among the tied
  small tier, MiniLM wins on throughput (3–4×) and footprint. The hashing
  baseline beating all dense models on hit@5 is an honest finding: this
  QA set shares exact vocabulary with the document, so **M3 will implement
  hybrid lexical+dense retrieval** (RRF fusion) rather than relying on
  dense-only recall; the dense model contributes paraphrase robustness that
  lexical matching lacks.
- **Alternatives:** BGE-M3 default (best dense quality, unacceptable CPU
  latency for an interactive demo); bge-small (equal quality, 4× slower);
  choosing by popularity/MTEB alone (violates the plan's benchmark-first
  requirement).
- **Consequences:** `EMBEDDING_MODEL=all-MiniLM-L6-v2` is the default in
  `.env.example`; switching to bge-m3 is a config change plus re-index
  (collections must be rebuilt when the embedder changes — embedding spaces
  are not mixable; the index script defaults guard this by rebuilding).

## D-015 — Byte-deterministic PDF rendering (reportlab `invariant`)
- **Decision:** `render_pdf._build_template` sets `rl_config.invariant =
  True` and an `invariant=` doc timestamp, so regenerating the corpus
  produces byte-identical PDFs.
- **Reason:** ReportLab otherwise stamps a random document `/ID` (and
  timestamps) into every build; chunk IDs embed the PDF sha256, so every
  regeneration silently invalidated the whole vector index. Discovered in
  M2 when `git status` showed the sample PDFs modified after a routine
  regeneration.
- **Alternatives:** Excluding the PDF hash from chunk IDs (loses
  content-change invalidation); post-processing the trailer (fragile).
- **Consequences:** Same dataset content → same bytes → same sha256 → same
  chunk IDs across machines and runs. PDFs generated before this fix differ
  from committed ones only in the trailer `/ID` (content identical); the
  next regeneration is stable forever after.

---

## Environment & commands (verified)

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe scripts/generate_dataset.py --force
./.venv/Scripts/python.exe scripts/process_sample_docs.py
./.venv/Scripts/python.exe scripts/build_vector_index.py --rebuild
./.venv/Scripts/python.exe scripts/evaluate_retrieval.py
./.venv/Scripts/python.exe scripts/benchmark_embeddings.py
./.venv/Scripts/python.exe -m pytest tests/        # 104 passed (1 opt-in)
```

## Open items / pending decisions

- **M3 hybrid retrieval:** dense-only retrieval underperforms the lexical
  baseline on this corpus (see D-014); M3 plans BM25/lexical + dense fusion
  (RRF) inside the existing `RetrievalService` contract.
- **M6 checks as quality goals:** start with the high-confidence core
  (undefined refs, dangling requires, duplicates, conflicting providers,
  orphans, unconsumed signals); add LLM-assisted checks only if meaningful.
- **LLM model choice for M3:** default `google/gemma-3-27b-it:free` via
  OpenRouter pending your API key; Ollama fallback config already stubbed
  in `.env.example`.
