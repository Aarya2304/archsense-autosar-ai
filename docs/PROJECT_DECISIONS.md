# PROJECT_DECISIONS

**Project:** ArchSense — AUTOSAR HLD Document Analysis Assistant (Tata Pulse Case Study 1 pilot)
**Created:** 2026-09-16 · **Status:** M0–M7 complete (M7 pending user review)
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
./.venv/Scripts/python.exe -m pytest tests/        # 195 passed (1 opt-in)
```

## D-016 — Hybrid retrieval: in-memory BM25 + RRF fusion (M3.1/M3.2)
- **Decision:** Hybrid retrieval = deterministic in-memory Okapi BM25
  (k1=1.5, b=0.75, shared `[a-z0-9]+` tokenizer + static stop list) over a
  mirror of the vector store, fused with dense results via Reciprocal Rank
  Fusion (`RRF(d) = Σ 1/(rrf_k + rank(d))`, rrf_k=60, ranks from 1).
  Ties break by (document_name, chunk_id); fusion is rank-based so raw
  scores are never mixed. `HybridRetrievalService` exposes
  hybrid/dense/lexical modes; hybrid is the copilot default, and the
  wrapped M2 `RetrievalService` is untouched (dense-only path preserved).
- **Reason:** D-014 showed lexical matching beats dense-only on this QA
  corpus; RRF is the standard, score-scale-free way to combine both
  without Elasticsearch-class infrastructure (D-006).
- **Alternatives:** rank-bm25 dependency (adds a package for ~80 lines of
  textbook code); score-level interpolation (requires score normalization,
  model-dependent); Convex Combination fusion (same normalization issue).
- **Consequences:** lexical index rebuilds automatically when the store
  count changes (`ensure_index`); ~2.8 ms/query lexical, ~15 ms/query
  hybrid; fused hits carry `rrf_score`, `sources`, and per-list ranks for
  UI/audit use.

## D-017 — LLM provider abstraction (M3.4–M3.6)
- **Decision:** `LLMProvider` protocol (`generate(system, user) ->
  LLMResponse[text, provider, model, latency_ms, usage, finish_reason]`)
  with three implementations: `MockLLMProvider` (deterministic offline
  evidence-extractor; default), `OpenRouterProvider` (OpenAI-compatible
  chat completions, temperature 0), `OllamaProvider` (local server via its
  `/v1/chat/completions` compatibility endpoint). Shared HTTP helper does
  bounded retries on 429/5xx only; config errors fail fast at construction
  with actionable messages; keys are never logged (redaction wrapper).
- **Reason:** Copilot/UI code must stay provider-agnostic (D-006's single
  provider interface); mock default keeps the repo runnable with zero
  credentials and makes the entire validation pipeline deterministically
  testable; `requests` was already a dependency, so no new packages.
- **Alternatives:** LangChain LLM wrappers (drags the dependency in,
  against D-006); provider SDKs (unnecessary); async providers (no
  concurrency requirement in the MVP).
- **Consequences:** swapping providers is a config change
  (`LLM_PROVIDER=openrouter|ollama|mock`); real-provider behaviour is
  verified through mocked HTTP in tests plus an optional live smoke test
  (M3.16); no API key exists in the environment, so no live OpenRouter
  call has been made yet (documented in IMPLEMENTATION_STATUS).

## D-018 — Citation architecture: mechanical validation only (M3.8–M3.10)
- **Decision:** Citations are evidence IDs (`E1`, `E2`, ...) assigned by
  the context builder in retrieval-rank order. The LLM may reference them
  inline (`[E1]`) and list them in `evidence_ids`, but every citation is
  resolved mechanically against the trusted ID→chunk map; metadata
  (document/version/section/pages/chunk id) is rendered exclusively from
  stored chunk metadata; quotes are extracted mechanically (token-overlap
  + difflib over the cited chunk's sentences), never written by the LLM.
  A grounded (non-refusal) answer with zero surviving citations fails
  validation, and ANY unknown evidence ID — inline `[En]` or declared in
  `evidence_ids` — fails the whole response (regression-hardened after
  review: one valid citation never rescues a fabricated one, e.g.
  `"claim [E999] [E1]"` → `validation_failure`, citations emptied).
  Structured JSON parsing tolerates fences/prose and degrades to an
  explicit `validation_failure` state — never silent acceptance.
- **Reason:** the case study's traceability requirement and the plan's
  hard rule: the LLM is never the source of truth for provenance.
- **Alternatives:** NLI-based citation verification (no local NLI model
  within budget); asking the LLM to output page numbers directly (that is
  exactly the hallucination we must prevent).
- **Consequences:** the copilot emits four structured outcomes
  (answered / insufficient_evidence / provider_failure /
  validation_failure); every citation is reproducible from
  (chunk_id, answer text) without the LLM.

## D-019 — Evidence gate: two-layer defense, honestly calibrated (M3.11)
- **Decision:** Pre-generation gate with three mechanical signals:
  (1) lexical-hit floor (`min_lexical_score=0.25` on bounded BM25),
  (2) IDF-weighted query-term coverage of the top-3 evidence chunks
  (`min_query_coverage=0.30`, light stemming, fixed question-word list),
  (3) optional lexical/dense agreement (default off — the calibrated grid
  showed no marginal value). Post-generation, the LLM's structured
  `insufficient_evidence` flag is the second layer. Thresholds were chosen
  from a ONE-SHOT grid (`scripts/calibrate_gate.py`, output
  `data/evaluation/gate_calibration.json`) and are not re-tuned.
- **Reason — measured on this corpus:** QA-U1 (brake pressure) has zero
  lexical hits; QA-U2 (OS scheduling) has 0.29 coverage — both are
  mechanically refused. QA-U3 (price) / QA-U4 (airbag) are topically
  adjacent and pass the gate **by design**: profiling showed NO
  token-overlap signal separates them from answerable questions
  (answerable coverage min 0.08–0.22 for QA-21/13/12 vs unanswerable max
  0.42–0.49 — overlapping distributions), so refusing them requires
  semantic judgment, which is the LLM layer's job. The chosen thresholds
  cost 1 false refusal (QA-21, coverage 0.22).
- **Alternatives:** strict thresholds (refuses up to 4 answerable
  questions); LLM-only refusal (no pre-generation defense, wasted calls);
  embedding-similarity floor (embedding-dependent, fragile across models).
- **Consequences:** with the offline mock, 2 of 4 negatives are refused by
  the gate and U3/U4-class questions are answered — a documented mock
  limitation, not a pipeline gap; with a real provider, the structured
  refusal path handles them (mechanics verified by tests). Not claimed:
  hallucination-proofness, 100% refusal coverage.

## D-021 — Extraction taxonomy derived from the corpus (M4.3/M4.28)
- **Decision:** Entity types = component, interface, port, signal,
  dependency, functional_flow. Predicates = provides (component→interface),
  requires (component→interface), depends_on (component→component, ALL
  component–component edges — the corpus's original requires/depends_on
  label is preserved as an attribute on the dependency entity), carries
  (interface→signal), implements (port→interface), participates_in
  (component→functional_flow). Types with no corpus support (requirement,
  data element as a distinct type, section, version) were NOT added.
- **Reason:** both questions in the brief — "does the corpus support an
  Entity/Fact distinction?" — answer YES richly: the HLD has 20/25/57/34/
  24/5 entities (v1) and explicit relationship tables; inventing extra
  types without content support would produce empty ontology classes.
- **Alternatives:** generic (subject, predicate, object) triples with free-
  form predicates (no mechanical domain/range checks possible);
  mirroring AUTOSAR's full metamodel (vastly over-scoped for 11 days).
- **Consequences:** predicates carry machine-checkable domain/range tables
  (`ExtractedFact.PREDICATE_DOMAIN/_RANGE`); the validator rejects
  type-violating facts instead of persisting them.

## D-022 — Extraction provenance: evidence IDs, never LLM metadata (M4.6)
- **Decision:** The extraction LLM sees evidence blocks `[EVIDENCE E1..En]`
  (same philosophy as M3) and may only reference evidence IDs; the app
  resolves IDs against the trusted ID→Source map built from chunk
  metadata. Unknown IDs → issue + candidate rejected (never silently
  accepted). The deterministic path embeds full `Source` objects directly.
- **Reason:** identical trust model to M3 citations (D-018); one mental
  model across the project, one validator philosophy.
- **Alternatives:** letting the LLM echo page/section (hallucination
  surface for zero benefit).
- **Consequences:** every persisted fact carries document/version/sha256/
  section/page-range/chunk_id from the chunk that yielded it; the
  traceability chain fact→chunk→document→version→section→page is queryable
  (`scripts/query_entities.py --fact ...`).

## D-023 — Deterministic-first extraction + provider-map owner attribution (M4.4)
- **Decision:** Five table handlers (3.1 catalogue → components with
  type/layer/description; 3.2.x port tables → ports + implements +
  provides/requires; 5 dictionary → signals + carries; 6.1 → dependency
  entities + depends_on; 4.x signal tables → name-only signals) plus prose
  patterns (component/interface/flow titles, Provider:/Consumers: lines,
  negation-guarded name-pair dependencies). Port-table owner attribution
  does NOT trust document order: the owner is the component that PROVIDES
  the table's P-port interface per the section-4 provider map; fallback
  for provides-less tables (CanDriver) = consumer-set intersection.
- **Reason:** the deterministic pass alone scores P=R=F1=1.000 on BOTH
  versions — the LLM is genuinely optional. Owner attribution had to be
  content-based because tables physically flow across component section
  boundaries on shared pages (the D-012 page-lumping lesson again: C-01's
  port table sits after C-06's title in reading order).
- **Alternatives:** LLM-first extraction (slow, non-deterministic, worse:
  ~0.75 confidence defaults); section-label-based attribution (broken by
  page lumping); nearest-preceding-title attribution (wrong owner for 5
  tables on this corpus).
- **Consequences:** name-pair prose facts carry the negation guard, so v2's
  planted D6 sentence ("does not require") never creates a fact; the v2
  planted D3 provider conflict is faithfully reflected (extraction reports
  what the document says — conflict *detection* is M6's job).

## D-024 — Extraction confidence: documented rules, not fake calibration
- **Decision:** Rule-based assignment: 0.95 explicit ID in a table row;
  0.90 explicit prose relationship ("Provider: C-xx") or relationship
  derived from table columns; 0.85 name-pair prose dependency
  (alias-resolved); 0.80 entity by name only (ID unknown at this stage).
  LLM candidates default to 0.75. `EXTRACTION_MIN_CONFIDENCE` (default
  0.5) is a rejection floor, not a probability.
- **Reason:** the brief explicitly forbids pretending an LLM number is
  mathematically meaningful; deterministic table matches are verifiably
  exact on this corpus, so they earn the top tier. No empirical
  calibration was performed and none is claimed.
- **Alternatives:** learned confidence (no training signal); uniform
  confidence (discards the real difference between an ID row and a name
  mention).
- **Consequences:** downstream consumers (M6 analysis, M7 diff) can filter
  by tier; confidence is stored on every entity/fact row and surfaced in
  the query CLI.

## D-025 — Registry persistence reuses the M1 typed schema (M4.10)
- **Decision:** Extraction output persists into the EXISTING M1 registry
  tables (components, interfaces, ports, signals, dependencies,
  functional_flows — all with (version_id, entity_id) uniqueness) plus ONE
  new uniform table `extraction_facts` (version_id, fact_key unique,
  subject/predicate/object, trusted provenance columns, indexes on
  subject/object/predicate). Every run writes an AnalysisRun(kind=
  "extraction") row and appends audit events (extraction_run_started /
  extraction_run_finished / extraction_registry_cleared).
- **Reason:** M1's schema anticipated exactly this subsystem; duplicating
  it as a parallel entity/fact store would fork the registry and violate
  the reuse instruction. The uniform facts table covers predicates with no
  typed home (participates_in, implements) without per-predicate tables.
- **Alternatives:** a generic EAV store (no FK-grade typing); Neo4j
  (explicitly out of scope, D-006).
- **Consequences:** re-running extraction upserts (idempotent, tested);
  `--reset` clears one version's extraction output (audited); the graph
  builder in M5 can join typed tables and extraction_facts directly.

## D-020 — M3 retrieval evaluation methodology (M3.3)
- **Decision:** `scripts/compare_retrieval_modes.py` scores lexical,
  dense and hybrid modes over the same 30-question GT set at K∈{1,3,5}
  (page/section hit rates, MRR, latency), writing
  `data/evaluation/retrieval_modes_comparison.json`. Same corpus, same
  questions, deterministic; no threshold tuning on the eval set.
- **Reason:** the M2 brief requires comparing modes and reporting
  honestly whether hybrid improves the baseline.
- **Alternatives:** only reporting the copilot end-state (would hide
  retrieval regressions); fabricating per-question relevance judgments.
- **Consequences — actual results (this corpus, MiniLM):** hybrid beats
  dense-only on every metric (page_hit@5 0.867 vs 0.800; section_hit@5
  0.833 vs 0.700; MRR@5 0.709 vs 0.658) and beats lexical at K=5 while
  losing section_hit@1 to lexical (0.467 vs 0.500) — reported, not hidden.

---

## D-026 — Graph is derived from the registry, never a second truth (M5)
- **Decision:** The M5 graph subsystem (`backend/graph/`) builds a
  NetworkX MultiDiGraph at request time directly from the M4 SQLite
  registry: nodes from the six typed entity tables, edges 1:1 from
  `extraction_facts` rows. No graph is persisted as source of truth; no
  relationship is ever invented, inferred, or LLM-supplied. `GraphService`
  is the only façade callers need.
- **Reason:** the brief's central M5 principle (registry → graph, not
  PDF → separate graph extraction); a persisted graph would fork truth
  the moment extraction is re-run.
- **Alternatives:** persisting node-link JSON as the working store (drift);
  Neo4j (out of scope since D-006; ~350-node corpus).
- **Consequences:** graphs are always reproducible (build ≈ 15–50 ms);
  M6 checks and M7 diff can query the registry OR the graph and agree by
  construction; JSON export to `data/graphs/` is an artifact, not a store.

## D-027 — MultiDiGraph keyed by M4 fact identity (M5)
- **Decision:** Graph type = `networkx.MultiDiGraph`; every edge is keyed
  by the M4 `fact_key` (the dedupe identity), preserving parallel facts
  (two distinct facts between the same ordered node pair remain two
  edges). Direction follows the M4 predicate table exactly
  (component→interface for provides/requires, component→component
  depends_on, interface→signal carries, port→interface implements,
  component→flow participates_in).
- **Reason:** a plain DiGraph silently collapses distinct facts; M7's
  graph diff must compare fact identities 1:1. The current corpus happens
  to contain no two facts on the same ordered pair (each port implements
  a distinct interface), but the guarantee must hold structurally, not by
  corpus luck — verified by a dedicated capacity test.
- **Alternatives:** DiGraph + edge-attribute lists (custom merge logic);
  undirected graph (loses provides-vs-requires orientation semantics).
- **Consequences:** all analysis is explicit about directed vs undirected
  semantics (e.g. `shortest_path(directed=True)` default,
  `neighbors_undirected` named as such); edge identity = fact_key enables
  set-diff in M7 with zero reconciliation logic.

## D-028 — Edge provenance: trusted registry snapshot on every edge (M5)
- **Decision:** Every graph edge carries a frozen provenance snapshot
  (document, version, section_no/title, page range, chunk id) taken from
  the `extraction_facts` columns — the SAME trusted chain M4 persisted —
  plus confidence and extractor. The pyvis edge popup renders it; no LLM
  output ever contributes provenance.
- **Reason:** continues D-018/D-022: provenance is resolved from stored
  metadata, never authored. "Where does this relationship come from?" must
  answer with a page, not a guess.
- **Alternatives:** looking provenance up lazily from the registry at
  render time (couples viz to the DB); trusting viz-layer text.
- **Consequences:** provenance survives filtering, JSON export, and HTML
  rendering verbatim; `GraphService.edge_provenance()` gives M8 a one-call
  citation lookup; GraphML flattens provenance to a citation line (GraphML
  cannot hold nested dicts — documented lossiness).

## D-029 — Version-scoped graphs by default (M5)
- **Decision:** `build_graph(version=...)` REQUIRES an explicit version
  label (fail-fast `ValueError` otherwise) and SQL-filters both nodes and
  edges by `version_id`. Mixing versions is impossible through the API.
- **Reason:** M7 (v1→v2 diff) depends on clean version separation; a
  blended default graph would be a silent contamination risk.
- **Alternatives:** an all-versions mode (documented as NOT provided —
  no current consumer; adding one later is trivial and would label
  nodes/edges per version explicitly).
- **Consequences:** evaluation proves isolation per version (0 foreign
  keys); v2's planted orphan C-05 correctly surfaces as a degree-0
  warning while v1 has none.

## D-030 — Dependency entities are degree-0 nodes by design (M5)
- **Decision:** In the graph, `depends_on` edges connect the two
  COMPONENTS directly (per the D-021 taxonomy); the DEP-nn dependency
  entities appear as nodes carrying `source_id`/`target_id`/`relationship`
  as METADATA and therefore have degree 0. Graph validation exempts
  dependency-typed nodes from the orphan warning (with an explanatory
  message) while still flagging real orphans (e.g. v2's planted C-05).
- **Reason:** the corpus stores the dependency's own label ("requires")
  as an attribute, not as a graph predicate — re-interpreting it as an
  edge type would duplicate the depends_on edge with different semantics
  (brief §27: preserve attributes, don't silently change meaning).
- **Alternatives:** dependency→component "documents" edges (invented
  predicate, violates derivation-only); hiding dependency entities from
  the graph entirely (loses the entity registry mirror).
- **Consequences:** 24 (v1) / 20 (v2) dependency nodes are degree-0
  WITHOUT being findings; the validator's warning distinguishes the two
  classes explicitly.

## D-031 — Findings are derived, deterministic, and ID-addressed (M6)

**Context:** M6 detects architecture issues. A naive approach treats the
LLM as the analyst; that breaks the project's trust model.

**Decision:** all M6 findings are produced by six mechanical detectors over
the trusted M4 registry + M5 graph (same derivation-only principle as
D-026). The LLM plays NO role in deciding that a finding exists. Each
finding carries a deterministic content-addressed ID
`M6-<TYPECODE>-<sha256[:10]>(version|type|entity_keys|fact_keys)` — same
inputs always yield the same ID, so re-runs are idempotent and M7 comparison
is a set operation. No random UUIDs.

**Consequences:** findings are reproducible; provenance is copied verbatim
from registry columns (D-022/D-028 chain continues); the validator (V1-V10)
recomputes and checks every ID.

## D-032 — Detector rule definitions (M6)

Exact rules (all deterministic, documented including their limitations):

- **UNDEFINED_REFERENCE (high):** a fact's subject or object canonical key
  has no entity row for the same version. Facts are reported, never
  silently dropped.
- **DANGLING_REQUIRES (high if the interface entity is itself undefined,
  else medium):** `component -> requires -> interface` with NO
  `component -> provides -> interface` fact in the same version.
  Asymmetry is deliberate: provision without demand is NOT a finding
  (D-027 graph keeps such interfaces connected).
- **DUPLICATE_INTERFACE (medium):** two or more DISTINCT interface entities
  whose normalized names collide. Repeated *references* are never
  duplicates (M4 already enforces unique `(version_id, entity_id)`).
- **CONFLICTING_PROVIDERS (high):** ≥ 2 distinct components hold `provides`
  facts for the same interface. The M4/M5 model has NO deployment/
  service-instance semantics, so every multi-provider case is flagged as a
  potential conflict — documented MVP limitation, not a claim about AUTOSAR.
- **ORPHAN_ENTITY (medium):** degree-0 component/interface/signal/
  functional_flow nodes in the version graph. Dependency nodes are excluded
  (degree-0 by design, D-030); ports are excluded (their linkage is fact-
  level; a factless port is an extraction gap, not an architecture issue).
- **UNCONSUMED_SIGNAL (medium):** a signal whose carrying interface has zero
  `requires` facts. The M4 model has no signal-level consumption predicate,
  so consumption is inferred transitively through the interface — the
  strongest defensible rule; per-signal reads are invisible to the model.

## D-033 — Severity and confidence (M6)

**Severity** reuses the M1 `FindingSeverity` enum (high/medium/low/info) —
the M6 task's "error/warning/info" sketch maps onto high/medium/info so no
parallel vocabulary is created. Mapping per detector is fixed in D-032; no
LLM and no numeric score is involved, and severity does NOT claim real-
world automotive safety impact.

**Confidence** is rule-based, not calibrated: a finding inherits the MAX
confidence of its supporting registry rows (max = the strongest witness;
simple, deterministic, monotone). Like M4 confidence it is a tier, not a
probability.

## D-034 — Findings persistence: reuse, idempotency, review preservation (M6)

The existing M1 `AnalysisRun(kind="analysis")` + `Finding` tables are used
as-is; no new tables. A run persists exactly what the CURRENT detectors
produce for one version: same deterministic finding_id → update in place,
carrying over `status`/`reviewer_comment`/`reviewed_at` (human review work
is never lost); findings no longer produced are removed from the new run's
record set. `--reset` deletes per version with audit events (M4 convention).
Version isolation is enforced at the single context-load point: a run can
only ever see one `DocumentVersion`'s rows.

## D-035 — Gold-set applicability in findings evaluation (M6)

The 8 planted v2 defects in the M0 ground truth are NOT all registry-level:
D3 (conflicting provider) and D5 (dropped signal) manifest only as
prose-vs-structure contradictions that M4's table-wins canonicalization
already resolved, and D1/D2/D6/D7/D8 are cross-version or prose checks
(M7 scope). Evaluation therefore derives applicability MECHANICALLY (a gold
defect counts only if its structural witness holds against the registry:
today only D4) and reports non-applicable gold with reasons instead of
counting them as false negatives. Fabricating recall against prose-only
defects would misrepresent capability; M7's cross-version diff is the
correct instrument for the rest.

## D-036 — Comparison scoping on the project, not the document row (M7)

The M1 schema models the synthetic corpus as one project containing two
Document rows (ABC_HLD_v1.0.0.pdf, ABC_HLD_v1.1.0.pdf), one
DocumentVersion each. "Versions from unrelated documents are rejected"
therefore maps onto PROJECT scope: `load_both_snapshots` requires both
versions to resolve under one project and rejects cross-project pairs.
Both labels are mandatory; base == target is rejected (a comparison needs
two distinct versions); unknown labels fail fast with the known-version
list. Version isolation is enforced at the single load point — every
snapshot, change and impact is scoped to exactly one DocumentVersion.

## D-037 — Change identity: canonical keys and fact triples (M7)

- **Entity identity** = the M4 canonical key (`component:C-05`). Added =
  key in target only; removed = key in base only; changed = key in both
  with a different typed-row display name (the only registry-level
  attribute change with stable identity; the v1→v2 corpus shows exactly
  one: the C-09 rename). Ordinary references never create entities.
- **Relationship identity** = the fact TRIPLE (subject, predicate,
  object, object_value) — NOT the raw fact_key. `fact_key` is the dedupe
  identity WITHIN one version; across versions a semantic change produces
  a new fact_key, and the task-preferred semantics fall out naturally:
  changed relationships surface as REMOVED old + ADDED new. No synthetic
  CHANGED_RELATIONSHIP exists. Within a version M4 guarantees one
  fact_key per triple, so triple sets are well-defined on both sides.
- **IDs** are content-addressed (`M7-ENT-ADD-<hash>`, `M7-REL-REM-<hash>`,
  `M7-IMP-<hash>` over change+entity+depth): reproducible, no UUIDs,
  re-running yields identical IDs so persistence and diffing are set
  operations.

## D-038 — Impact analysis: documented traversal rule (M7)

Anchors follow the change: the changed entity for ENTITY_*; both fact
endpoints for RELATIONSHIP_*; traversal runs in the TARGET graph for
ADDED/CHANGED changes and the BASE graph for REMOVED (where the change is
real). Depth 0 = anchor (DIRECT); depth 1 neighbors take the category of
the first edge used (DEPENDENCY / INTERFACE_CONSUMER / INTERFACE_PROVIDER
/ SIGNAL / FUNCTIONAL_FLOW); depth >= 2 is TRANSITIVE. BFS visits edges
sorted by fact_key; each (change, entity) pair yields ONE impact at its
minimum depth. Every recorded path step is a REAL stored edge, kept in
walk order with forward/reverse direction, so V8 can re-verify each step
against the scope graph — no invented hops. Wording is deliberately
"potentially impacted": a graph-neighborhood statement, never a claim of
functional breakage. Default depth 1 (conservative), `--depth` to widen.

## D-039 — Comparison persistence: CompareRun upsert (M7)

Reuses the existing M1 `compare_runs` table (present since M1, never
previously populated) — no new tables. One row per (base, target) pair;
re-running upserts `results_json` (payload embeds its params: depth,
generator), so the store never grows duplicate comparisons. The registry
remains the source of truth: any comparison is reproducible by rerunning
the deterministic comparator; persistence is a convenience cache for M8.
persist/clear append audit events via the existing audit-log helper.

## D-040 — Revision evaluation: mechanical gold + honest applicability (M7)

Gold comes from the GT registries themselves (the renderer's
source-of-truth): entity added/removed = ID-set difference across all six
families; entity changed = name-differs-with-stable-ID; relationship
gold = the M4-evaluation fact derivation run on both GT versions and
set-differenced (65 removed / 43 added, exactly reproduced by the
comparator). Two documented `expected_diff` blind spots are cross-checked
mechanically instead of papered over: it has no ports family (the 5
removed ports P-053..P-057 come from `gt['ports']`), and its four
"modified" interfaces (IF-03/13/20/24) differ in provider/consumers —
fact-level attributes that are scored under relationship changes, where
the evidence actually lives. Impact gold = independent replay of the
D-038 rule over the gold fact graphs (per side), plus explicit D1/D8
anchor assertions. Applicability stays honest (continuing D-035): D1
(removed dependency) and D8 (new consumer) are structurally applicable
and detected; D2 is not applicable — M4 canonicalized consumers to
stable IDs, and the stale name survives only as free text in
`component:C-09.description` (STALE_REFERENCE requires a fact endpoint,
not prose); D3/D5/D6/D7 remain prose-vs-structure defects with reasons,
never false negatives. Change is NOT defect: RELATIONSHIP_REMOVED is
reported as a change; findings arise only from explicit rules
(stale_reference today).

## D-041 — M8 UI architecture: thin adapters over M0–M7 facades (M8)

The Streamlit app (`app/`) is a consumer, never a second implementation:
`screens/` render, `services/` adapt, `components/` format. Every screen
delegates to the existing UI-agnostic facades (M5 `GraphService`, M6
`FindingEngine`/persistence, M7 comparator, M3 copilot, M1 storage) — no
backend algorithm is re-implemented in UI code, no new database, no FastAPI.
`app/screens/` (not `app/pages/`) is deliberate: Streamlit auto-treats a
`pages/` directory as a multipage app, which would bypass the app's own
state-aware router.

## D-042 — M8 caching policy (M8)

Immutable resources are cached per session with `st.cache_resource`
(engine/session factory, embedder, Chroma collection, graphs). Mutable
analysis results (fresh M6 runs, M7 comparisons, copilot answers, reports)
are never cached by decorator — they live in session state, are produced by
explicit user actions, and are invalidated on version change (D-043).
Sentence-transformer load cost is paid once per process/session, not per
interaction.

## D-043 — Version change invalidates stale selections (M8)

Entity keys, findings, comparisons, answers and reports are version-scoped,
so `set_version()` clears all of them unconditionally on change and resets
the workspace to page 1. The UI must never display v1.0.0 data under a
v1.1.0 selection. Shared version selectors are index-driven from
`as_version` (no widget keys) so every screen observes the same value.

## D-044 — UI renders only backend-generated HTML/SVG (M8)

The only HTML-bearing surface is the PyVis graph (backend-generated,
`cdn_resources="in_line"`, rendered via `st.iframe`) and the PyMuPDF page
SVG (backend-generated string in `st.iframe`). No user-provided string is
ever passed to an HTML-rendering element, so there is no HTML-injection
path from the UI.

## D-045 — Exports contain deterministic facts, never secrets (M8)

Reports assemble sections 1–9 from M1–M7 data only; LLM output never fills
factual fields. No API keys, environment variables or stack traces are
included; downloads are offered via `st.download_button` and mirrored under
git-ignored `data/exports/`.

## D-046 — M3 hybrid version-filter fix (M8 compatibility fix)

During M8 verification a real M3 bug surfaced: in hybrid mode the store-level
`version` equality filter applied only to the dense leg; the lexical (BM25)
leg retrieved across all versions, so a v1.1.0 copilot answer could cite
v1.0.0 chunks. Fixed minimally in `backend/rag/hybrid.py` (equality keys
must filter both legs). This is a correctness fix, not a redesign: the M3
API, RRF fusion and gate are unchanged, and all M3 tests pass. Verified:
v1.1.0 answers cite only v1.1.0 evidence.

## D-047 — M9 upload architecture: dedicated runtime store, isolated index (M9)

User PDFs are stored under `data/uploads/` (gitignored; `.gitignore` gained
an explicit `data/uploads/` rule because the `!data/` whitelist would
otherwise re-include it), processed JSON under `data/uploads/processed/`,
and indexed into a **dedicated upload Chroma collection**
(`UPLOAD_COLLECTION`, default `uploads_chunks`) so `data/vectors/chroma/`
and the external AUTOSAR regression index are never touched. Storage is
content-addressed: SHA-256 per file, safe filename
(`{digest8}_{sanitized}`), duplicate *content* is detected against the
registered `Document.sha256` and reported (with a `--reset` escape hatch)
instead of silently re-ingesting. Uploads are PDF-only, size-capped
(`MAX_UPLOAD_MB`, default 100) and never executed.

## D-048 — Profile detection is evidence-based, not name-based (M9)

Each upload is classified into one of three profiles from *document
evidence* (title metadata, section titles, table headers, repeated prose
terminology, ID-shape conventions) — never from a single keyword hit:

- `application_hld` — the synthetic ABC HLD schema (M4 taxonomy: C-/IF-/
  P-/SG-/DEP-/FL- IDs, catalogue tables, "S-R/C-S interface" prose).
- `autosar_adaptive_platform` — strong AUTOSAR Adaptive Platform
  terminology across title + section structure (e.g. "Adaptive
  Platform", "Functional Cluster", "ARA" recurring in section titles).
- `generic` — everything else: M1/M2/M3 only; **no structured M4–M6
  results are fabricated** for it. Zero findings is a valid result; a
  wrong analysis is not.

Detection is deterministic and unit-tested against the real ABC HLDs, the
real R20-11 AUTOSAR document and a deliberately generic PDF.

## D-049 — AUTOSAR schema: small ontology from real evidence (M9)

The R20-11 document is NOT forced into the synthetic ABC entity schema. A
small AUTOSAR-specific vocabulary (10 entity types, 10 predicates) was
derived from verified prose of
*AUTOSAR Explanation of Adaptive Platform Design, R20-11, Document ID 706*
(`backend/extraction/autosar/`): `adaptive_application`, `ara`,
`functional_cluster`, `platform_foundation`, `platform_service`,
`service`, `interface`, `process`, `machine`, `manifest`,
`software_package`; predicates `runs_on`, `provides_interface`,
`uses_interface`, `provides_service`, `interacts_with`, `belongs_to`,
`configured_by`, `implemented_as`, `commands`, `updates`. Extraction is
deterministic (line-windowed sentence patterns, negation-aware, ToC
pages excluded), every fact carries trusted `EvidenceRef` provenance
(chunk → document/section/page) under the same D-022 rules as M4 — the
LLM is never involved and can never invent page/section/chunk identity.
Entities persist in the new generic `extraction_entities` table, facts in
the existing `extraction_facts` table, so M5/M6/M7 consume AUTOSAR data
through the exact same registry contracts as ABC data.

## D-050 — Profile-aware analysis routing (M9)

M5 graph building, M6 findings and M7 comparison are profile-aware:
- M5 nodes now load both the M4 typed tables *and* generic
  `extraction_entities` rows; graph validation accepts the union of
  profile predicate domains.
- M6 runs the ABC detector suite only for `application_hld`; AUTOSAR
  versions get the conservative AUTOSAR set (`inconsistent_classification`,
  `undefined_reference`) — ABC rules are never applied to AUTOSAR facts.
- M7 comparison requires both versions to share one profile (enforced in
  the service gate and the UI); cross-profile comparisons are refused
  with an actionable message.
- An upload without a detected version gets a unique
  `unversioned-{digest8}` label, because version labels are only unique
  per document while M0–M8 services resolve versions by label.

## Open items / pending decisions

- **M7 diff:** edge identity = `fact_key` (D-027) makes the v1→v2 edge
  diff a set operation; node diff uses canonical keys directly. The
  remaining planted defects (D1 removed dependency, D2 stale reference,
  D6 contradiction, D7 prose/table mismatch, D8 impact-relevant change)
  are the natural M7 gold set — see D-035.
- **Real-provider smoke tests (copilot + extraction):** OpenRouter
  reachable (M8 verification performed one real round-trip; provider/model
  resolved, LLM judged the demo question outside the gated evidence — no
  fabricated answer). Extraction smoke test still pending; default copilot
  model `google/gemma-3-27b-it:free` (M8 env used
  `nvidia/nemotron-3-super-120b-a12b:free`).
- **D2 stale-name witness:** detectable only from prose (the old C-09
  name inside `component:C-09.description`); a prose-level detector
  (RAG-text based, outside the structured registry) could close this —
  deferred as out of M7's structural scope.
- **Registry name hygiene:** v2's C-09 display name extracted as
  `VehicleModeMgrSW C` (stray space from PDF text extraction); identity,
  keys and diffs are unaffected. A registry-side whitespace-normalization
  pass could clean it without touching M0/M1.
- **AUTOSAR gold standard:** none exists for the R20-11 document, so M9
  reports extraction coverage and hand-verified spot checks (e.g. ARA
  section 3.1.1, pp. 15–16) — not precision/recall. The ontology is
  intentionally small and explanatory-document-scoped; it does not claim
  general AUTOSAR understanding.
- **pyvis 0.3.2 template hygiene:** its HTML template hardcodes two dead
  resource blocks (commented node_modules/vis pair, cosmetic bootstrap CDN
  tags); `render_html` strips them deterministically so exports are fully
  resource-free. Re-check the strip patterns when upgrading pyvis.
