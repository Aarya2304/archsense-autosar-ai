# IMPLEMENTATION_STATUS

**Updated:** 2026-09-18 (M0–M8 complete: all milestones implemented, tested and verified)
**Deadline:** 2026-09-26 · M8 delivered; remaining time is for review, demo dry-runs and submission.

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

- *(nothing — M8 complete; all eight milestones delivered)*

## Next up (requires approval)
- *(none — project milestones complete; remaining work is review, demo
  dry-runs, traceability matrix and submission per the schedule below)*

## M7 — Revision Compare / Impact Analysis (complete)

### Architecture

```
DocumentVersion (1.0.0)     DocumentVersion (1.1.0)
  M4 registry snapshot  x  M4 registry snapshot       load_both_snapshots
        └───────────┬───────────┘    (same-project check, D-036)
                    ▼
         RevisionComparator.compare(base, target, depth)   comparator.py
          ├─ entity diff        canonical keys  → 0 add / 16 rem / 1 chg
          ├─ relationship diff  fact triples    → 43 add / 65 rem
          ├─ impact analysis    BFS, real-edge  → 974 items @ depth 1
          │                     paths, D-038 categories
          ├─ revision findings  stale_reference rule
          └─ mechanical validation V1–V9 (real-graph path re-check)
                    ▼
  RevisionComparison (deterministic IDs M7-*; sorted; to_json())
                    ▼ persist_comparison → compare_runs upsert (D-039)
```

### Measured results (1.0.0 → 1.1.0, depth 1)

| change family | gold | detected | TP | FP | FN | P | R | F1 |
|---|---|---|---|---|---|---|---|---|
| entity_added | 0 | 0 | 0 | 0 | 0 | – | – | – |
| entity_removed | 16 | 16 | 16 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| entity_changed | 1 | 1 | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| relationship_added | 43 | 43 | 43 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| relationship_removed | 65 | 65 | 65 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| impact | 137 | 137 | 137 | 0 | 0 | 1.000 | 1.000 | 1.000 |

Planted-defect verdicts: **D1** (removed dependency) and **D8** (new
consumer of changed interface) structurally applicable and detected with
impact anchors reaching the expected entities; D2/D3/D5/D6/D7
not-applicable with documented reasons (D-040) — prose-vs-structure
defects, never counted as false negatives.

### Performance (measured)

Context ≈ 140–170 ms (both registries + graphs); entity+relationship diff
≈ 6–8 ms; impact (974 items) ≈ 35–45 ms; validation ≈ 45–55 ms; total
≈ 135–270 ms. Deterministic: repeated runs produce byte-identical JSON
(modulo timings).

### Files

- Created: `backend/diff/{__init__,models,context,entity_diff,
  relationship_diff,impact,revision_findings,validation,comparator,
  persistence,evaluation}.py`, `scripts/compare_revisions.py`,
  `scripts/evaluate_revisions.py`, `tests/test_diff_models_diffs.py`,
  `tests/test_diff_comparator_impact_eval.py`
- Modified: `docs/PROJECT_DECISIONS.md` (D-036…D-040),
  `docs/IMPLEMENTATION_STATUS.md`, `README.md`. No M0–M6 code touched.

### Verification (all run)

- Full suite: **403 passed, 1 deselected** (was 348; +55 M7 tests,
  nothing removed or weakened)
- CLI: `compare_revisions.py --base-version 1.0.0 --target-version
  1.1.0` (summary + provenance), `--json`, `--persist` (created →
  updated idempotency, same compare_run_id), `--reset`, `--depth 2`
  (3144 impacts, validation clean), reversed pair works,
  same-version rejected with exit 2
- `evaluate_revisions.py --save` → `data/evaluation/revision_evaluation.json`
- Version isolation: every change/impact scoped to exactly one version
  (tested structurally); same-project enforcement tested
- External doc `AUTOSAR_EXP_PlatformDesign.pdf`: M7 comparison NOT
  applicable — no M4 structured extraction exists for it (unchanged
  since M6)

### Limitations

- D2's stale-name witness is prose-only (M4 canonicalized consumers to
  stable IDs); STALE_REFERENCE requires a fact endpoint, so D2 is
  structurally undetectable (documented, not fabricated).
- Impact items are graph-neighborhood statements ("potentially
  impacted"), not functional breakage claims; categories are rule tiers.
- ENTITY_CHANGED covers typed-row renames only; fact-level attribute
  changes surface as REMOVED+ADDED pairs by design (D-037).
- v2 C-09 display name carries a PDF-extraction space
  (`VehicleModeMgrSW C`); identity/keys unaffected (open item).

## Test status

```
348 passed, 1 deselected (~35s)       # default: fast + deterministic suite
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
tests/test_extraction_schema_deterministic.py 24 passed (M4)
tests/test_extraction_llm.py      17 passed   (M4)
tests/test_extraction_service_storage.py 20 passed (M4)
tests/test_graph_builder.py       41 passed   (M5)
tests/test_graph_viz_eval.py      13 passed   (M5)
tests/test_findings_models_detectors.py   34 passed   (M6)
tests/test_findings_engine_persistence_eval.py   14 passed   (M6)
```

## M4 — Structured extraction (this milestone)
- [x] **Taxonomy from the corpus (M4.3, D-021)** — 6 entity types
      (component/interface/port/signal/dependency/functional_flow), 6
      predicates with mechanical domain/range tables (provides, requires,
      depends_on, carries, implements, participates_in); unsupported types
      (requirement, data element) deliberately NOT added
- [x] **Pydantic schema (M4.2)** `backend/extraction/models.py`:
      `Source` (frozen trusted provenance), `EvidenceRef` (evidence ID or
      resolved source), `ExtractedEntity` / `ExtractedFact` with
      confidence bounds [0,1], normalization (`normalize_key`),
      deterministic dedupe keys
- [x] **Deterministic extractor (M4.4, D-023)**
      `backend/extraction/deterministic.py`: 5 table handlers + prose
      patterns; owner attribution via the section-4 provider map (page-
      flowed tables are NOT owned by the nearest preceding title);
      negation guard keeps v2's planted D6 sentence out of the facts
- [x] **LLM extraction (M4.5–M4.6, M4.13–M4.14)**
      `backend/extraction/context.py` + `llm.py`: evidence-ID contract
      identical to M3; structured JSON {entities, facts}; unknown evidence
      IDs rejected; MockExtractionProvider (deterministic, failure/
      malformed/unknown-ID injection); OpenRouter/Ollama via the M3
      factory unchanged
- [x] **Mechanical validator (M4.7)** `backend/extraction/validator.py`:
      evidence resolution (unknown/missing → reject), reference existence,
      domain/range violation checks, confidence floor, deterministic
      dedupe (best confidence wins); alias canonicalization merges
      name-form and ID-form keys
- [x] **Confidence model (M4.8, D-024)**: documented rule tiers
      (0.95 table-with-ID / 0.90 explicit prose or derived table /
      0.85 name-pair prose / 0.80 name-only / 0.75 LLM default);
      `EXTRACTION_MIN_CONFIDENCE` rejection floor (default 0.5);
      no fake empirical calibration claimed
- [x] **Normalization + dedupe (M4.9)**: case-insensitive ID keys
      (c-02 → component:C-02), name→ID alias map, dedupe on
      `subject|predicate|object|object_value`
- [x] **SQLite registry (M4.10–M4.11, D-025)**: populates the EXISTING M1
      typed tables + new `extraction_facts` table (unique per version on
      fact_key, indexed on subject/object/predicate); AnalysisRun rows +
      append-only audit events; idempotent re-runs; audited `--reset`
- [x] **ExtractionService (M4.12)** `backend/extraction/service.py`:
      deterministic → optional LLM → validate → persist orchestration with
      stage timings; provider/parse failures SURFACE as issues (test-
      verified, not swallowed); structured summary
- [x] **CLIs (M4.15/M4.16)**: `scripts/extract_entities.py`
      (--document/--version/--llm/--provider/--deterministic-only/--reset/
      --no-persist/--json/--min-confidence) and `scripts/query_entities.py`
      (--entities/--entity/--related/--predicate/--fact/--version/--json);
      full provenance trail fact→chunk→document→version→section→page
- [x] **Evaluation (M4.17–M4.18)** `backend/extraction/evaluation.py` +
      `scripts/evaluate_extraction.py`: gold derived mechanically from GT
      registries; entity/fact P/R/F1 micro+per-predicate, provenance
      accuracy, dedupe counts, per-example detail; **entities and facts
      P=R=F1=1.000 on BOTH versions, provenance accuracy 1.000**
- [x] **Tests (M4.19)**: 54 new tests (schema+deterministic 24, LLM 17,
      service/storage 20) incl. exact GT-equality on both versions,
      idempotent persistence, reset, audit trail, unknown-evidence
      rejection; all 198 pre-M4 tests still pass

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

## M5 — Architecture Graph Explorer (this milestone)
- [x] **Derived-only graph (D-026)** `backend/graph/builder.py`: nodes
      from the six typed registry tables, edges 1:1 from
      `extraction_facts`; nothing persisted, nothing invented
- [x] **MultiDiGraph keyed by fact_key (D-027)** — parallel facts can
      never collapse; capacity test proves 2 facts on one pair stay 2
      edges; direction follows the D-021 predicate table exactly
- [x] **Version-scoped builds (D-029)** — explicit `version=` required
      (fail-fast), SQL-filtered nodes AND edges; cross-version
      contamination impossible through the API (tested: DEP-19, IF-08/09,
      SG-015 absent from v2 graph)
- [x] **Trusted provenance on every edge (D-028)** — frozen snapshot
      (document/version/section/pages/chunk) + confidence + extractor;
      provenance verified against registry rows; survives filtering,
      JSON export and HTML popups verbatim
- [x] **Mechanical validation** `validation.py`: dangling endpoints
      (NX auto-node-aware), invalid predicates, missing/version-mismatched
      provenance, duplicate fact identities, registry↔graph 1:1
      cross-check, canonical-key warnings, orphan detection with the
      D-030 dependency exemption (v2's planted C-05 surfaces as warning)
- [x] **Analysis** `analysis.py`: degrees, directed/undirected neighbors,
      predecessors/successors, shortest path (directed default), weakly
      connected components, related-view grouped by predicate with
      provenance, friendly-key resolution (C-02 / component:C-02 /
      display name)
- [x] **Filtering** `filtering.py`: entity_type / predicate /
      min_confidence / ego-neighbourhood with depth / version assert —
      all non-mutating, attributes preserved verbatim
- [x] **Export** `export.py`: node-link JSON (statistics included,
      re-hydratable) + GraphML with flattened provenance citation line
- [x] **pyvis visualization** `visualization.py`: standalone HTML with
      inlined vis-network (no CDN), UTF-8 write (bypasses pyvis's
      cp1252 save bug), per-type node colors, per-predicate edge colors,
      provenance/confidence/extractor in every edge popup; dead template
      resource blocks stripped — zero external references
- [x] **GraphService** `service.py`: build/validate/filter/related/path/
      stats/export/render façade for CLI, M6/M7 and M8
- [x] **CLIs**: `scripts/graph_explorer.py` (--stats/--related/--path/
      --predicate/--type/--confidence/--node+--depth/--render/--json/
      --export-graphml) and `scripts/evaluate_graph.py` (both versions,
      human + --json)
- [x] **Evaluation (M5.20)** `evaluation.py`: gold derived mechanically
      from GT registries; **nodes and edges P=R=F1=1.000 on BOTH
      versions** (165/200 and 149/178), version isolation clean,
      provenance correctness 1.000, registry↔graph 1:1 (0 missing/
      extra/duplicate), eval ≈ 1 ms
- [x] **Tests**: 54 new (builder/provenance/validation/analysis/
      filtering/export/service 41, viz/eval 13); all 252 pre-M5 tests
      still pass → 300 total

## M5 performance (actual, per version)

| stage | v1.0.0 | v1.1.0 |
|---|---|---|
| registry load + graph build | ~15–50 ms | ~15–50 ms |
| validation (with registry cross-check) | <5 ms | <5 ms |
| JSON export (~1 MB) | <40 ms | <40 ms |
| pyvis HTML render (~830 KB) | ~300 ms | ~300 ms |
| evaluation | ~1 ms | ~1 ms |

## M5 known limitations
- The 1.000 graph scores inherit M4's perfect extraction on the synthetic
  corpus; real HLDs will not score 1.000.
- No all-versions graph mode (deliberate, D-029); M7 will add explicit
  version-labeled comparison instead.
- GraphML provenance is flattened to a text line (format limitation);
  JSON export is the lossless machine format.
- pyvis 0.3.2 template carries dead resource blocks; `render_html`
  strips them (strip patterns documented in D-030/open items — recheck
  on pyvis upgrades).
- Dependency entities render as degree-0 nodes (D-030 semantics); the
  M8 UI may visually de-emphasize them.

## Known bugs

- *(none open)* — M4 fixes during development: LLM parse/provider failures
  were silently swallowed by the service (now surfaced as issues);
  `session.get(model, None)` SAWarning in registry persistence; port-table
  owner attribution initially trusted document order (5 tables on shared
  pages got the wrong owner) — fixed via the section-4 provider map;
  fact-key trailing-segment normalization in the query CLI.

## Blockers

- *(none)* — OpenRouter key not present in the environment, so live
  provider smoke tests (M3.16 copilot, M4.14 extraction) have NOT been
  run; both paths are verified via mocked HTTP/offline tests. Run
  `scripts/ask_copilot.py "<q>" --provider openrouter` or
  `scripts/extract_entities.py --provider openrouter --llm` once a key is
  configured in `.env`.

## M8 — Final Streamlit application (COMPLETE)

**Delivered:** `app/` package — `main.py` (router), `state.py` (session
state, D-043), `navigation.py`, `components/helpers.py`, `services/`
(`app_services.py` adapters over M1–M7, `report_service.py` deterministic
report assembly) and seven screens under `screens/`: Dashboard, Document
Workspace, Architecture Explorer, Copilot, Findings, Revision Compare,
Export & Report. The app reuses every backend facade — no algorithm is
duplicated in the UI (D-041); caching per D-042; exports per D-045.

**Startup:** `.venv/Scripts/python.exe -m streamlit run app/main.py`
(headless mode supported; default port 8501).

**Verification:** Streamlit `AppTest` battery `scripts/verify_app_screens.py`
— **61 checks, 61 passed**: all seven screens render without exception,
navigation + dashboard cards work, workspace page/section/version behavior,
explorer filters (165/200 full → 20/24 with `component` filter; confidence
1.0 drops all edges), copilot grounded answer + citations with version
isolation, findings v1.0.0 clean / v1.1.0 `orphan_entity` with trusted M4
provenance, revision compare 0/16/1 + 43/65 with D1 `depends_on` removal in
the change table (974 → 233 impacts at depth 0), export JSON/CSV with all
sections and no secrets, and stale-state clearing on version switch.

**Compatibility fix:** M3 hybrid retrieval leaked cross-version evidence in
the lexical leg (D-046, fixed minimally in `backend/rag/hybrid.py`); all
M0–M7 tests pass unchanged.

**Tests:** **435 passed, 1 deselected** (was 403; +32 M8 tests).
Screen battery: `python scripts/verify_app_screens.py` (exit 0).

**Limitations:** findings review stays read-only in this UI (safe per-finding
review mutation is not exposed by M6 yet); findings screen shows persisted
state read-only or a fresh uncached run, never silently; external AUTOSAR
PDF still has no M4 extraction, so the UI shows its ingestion/RAG status
only — no fabricated entities/findings/graph for it.

## Remaining work (milestone view)

| Milestone | Scope | Planned | Status |
|---|---|---|---|
| M3 | Hybrid RAG + citation validator + refusal | Sep 19 | ✅ complete |
| M4 | Structured extraction + registry + evaluation | Sep 20 | ✅ complete |
| M5 | Graph explorer from the extraction registry | Sep 21 | ✅ complete |
| M6 | Deterministic checks + findings review UI | Sep 22 | done (backend + CLI; read-only review UI in M8) |
| M7 | Revision compare + impact analysis | Sep 24 | done (backend + CLI + evaluation) |
| M8 | Streamlit application: 7 screens, exports, verification | Sep 24 | done (UI + services + 61-check screen battery) |
| — | Docs, traceability matrix, Drive submission | Sep 25 | — |
| — | Demo dry-runs, backup | Sep 26 | — |

## Notes for evaluators / demo

- The copilot's refusal story is demonstrable offline: brake-pressure
  questions are refused *before* any LLM call with a mechanical reason
  (`no strong lexical match ... question may be outside the corpus`).
- Citation provenance is mechanically enforced: the model can only
  reference evidence IDs; document/version/section/page/quote all come
  from stored chunk metadata — fabricated IDs are rejected by tests.
- M4 adds the queryable-registry demo: `query_entities.py --fact ...`
  prints the full traceability chain for any fact (chunk → document →
  version → section → page), and extraction scores P=R=F1=1.000 against
  the ground truth on both HLD versions — quantitative, not anecdotal.
- M5 adds the visual demo: `graph_explorer.py --version 1.0.0 --render`
  produces a standalone interactive HTML (no internet needed — open the
  file from a USB stick); hovering ANY edge shows document, version,
  section, page and chunk id — "where does this relationship come from?"
  answered in one hover. `--related C-02` and `--path C-02 IF-01` give
  the deterministic query story; `evaluate_graph.py` shows P=R=F1=1.000.
- Everything regenerates from scratch in <60 s via the three dataset
  commands; extraction runs in ~17 ms per version (deterministic, no LLM);
  registry persistence ~0.3 s per version.

---

## M6 — Findings / Architecture Analysis (complete)

**Scope delivered:** deterministic architecture analysis over the trusted
M4 registry + M5 graph. Six detector families, mechanical validation
(V1–V10), idempotent persistence into the EXISTING M1
`AnalysisRun(kind="analysis")` + `Finding` tables (no new tables), version
isolation enforced at the single context-load point, CLI + evaluation.
No LLM, no network, no PDF parsing anywhere in the M6 path.

### Architecture

```
SQLite M4 registry + M5 graph  (trusted, version-scoped)
        |  build_analysis_context() — one-shot load, prebuilt maps
        v
FindingEngine (backend/findings/engine.py)
        |  six detectors (detectors.py, D-032 rules)
        v
Finding (models.py, deterministic IDs M6-<TYPE>-<hash>, D-031)
        |  validate_findings() V1–V10 (validator.py)
        v
persist_findings() — AnalysisRun(kind="analysis"), idempotent,
        review-state preserving (persistence.py, D-034)
        v
scripts/analyze_findings.py · scripts/evaluate_findings.py
```

### Detector results on the synthetic corpus (measured)

| version | entities | facts | findings |
|---------|----------|-------|----------|
| 1.0.0   | 165      | 200   | 0 (clean baseline) |
| 1.1.0   | 149      | 178   | 1 (orphan `component:C-05` — planted D4) |

Evaluation vs ground truth (`scripts/evaluate_findings.py`): v1
TP=FP=FN=0; v2 applicable gold = 1 (D4), detected 1, **precision =
recall = F1 = 1.000**. Six of the eight planted defects (D2, D3, D5,
D6, D7, D8) are **not applicable to v2 registry-level M6**: they are
prose-vs-structure or cross-version defects — D3/D5 were already resolved
by M4's documented table-wins canonicalization, the rest are M7 scope
(D-035). (The remaining planted defect, D1, is a v1 gold entry and is
likewise reported not-applicable in v1's evaluation.) All not-applicable
defects are reported with reasons, never counted as false negatives.

### Performance (measured, per version)

Context build ≈ 12–40 ms; all six detectors + validation ≈ 0.7 ms;
total ≈ 13–41 ms. SQLite persist of a run ≈ 5 ms. No DB scans inside
detector loops (prebuilt maps).

### Files

- Created: `backend/findings/{__init__,models,context,detectors,validator,engine,persistence,evaluation}.py`,
  `scripts/analyze_findings.py`, `scripts/evaluate_findings.py`,
  `tests/test_findings_models_detectors.py`,
  `tests/test_findings_engine_persistence_eval.py`
- Modified: `docs/PROJECT_DECISIONS.md` (D-031…D-035),
  `docs/IMPLEMENTATION_STATUS.md`, `README.md`. No M0–M5 code touched.

### Verification (all run)

- Full suite: **348 passed, 1 deselected** (was 300; +48 M6 tests,
  nothing removed or weakened)
- `analyze_findings.py --version 1.0.0` → 0 findings (baseline)
- `analyze_findings.py --version 1.1.0` → D4 orphan, provenance
  `ABC_HLD_v1.1.0.pdf §3.1 p4`, confidence 0.95
- `--persist` idempotency: second run `persisted=1 carried_review_state=1
  removed=0`; `--reset` removed 2 rows then re-persisted cleanly
- Version isolation: v1 run sees no v2 facts (tested structurally)
- JSON mode emits the full machine-readable structure (validation,
  counts, timings, findings with evidence + provenance)
- External doc `data/external_test/AUTOSAR_EXP_PlatformDesign.pdf`: NOT
  analyzable by M6 — it has no `DocumentVersion` row and no M4 structured
  extraction; M6 honestly reports this rather than inventing a workaround.

### Limitations

- Only D4 of the eight planted defects is structurally witnessed in the
  registry; D3/D5 applicability is blocked by M4's table-wins
  canonicalization and the absence of signal-level consumption (D-035).
- CONFLICTING_PROVIDERS flags every multi-provider interface; the model
  cannot distinguish legitimate AUTOSAR deployment/service-instance
  setups (documented in D-032, not hidden).
- Confidence/severity are rule-based tiers, not calibrated probabilities
  or safety impact ratings.
- Review workflow statuses exist in storage; the UI to change them is M8.
