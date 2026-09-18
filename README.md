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
| M4 | Structured extraction + SQLite registry + evaluation | ✅ complete |
| M5 | Architecture Graph Explorer (NetworkX + pyvis) | ✅ complete |
| M6 | Deterministic architecture findings + analysis CLI | ✅ complete |
| M7 | Revision compare + impact analysis (deterministic diff) | ✅ complete |
| M8 | Final Streamlit application | planned |

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
.venv/Scripts/python scripts/analyze_findings.py --version 1.1.0      # deterministic findings (M6)
.venv/Scripts/python scripts/evaluate_findings.py                     # findings P/R/F1 vs planted defects
.venv/Scripts/python scripts/compare_revisions.py \
    --base-version 1.0.0 --target-version 1.1.0           # revision diff + impact (M7)
.venv/Scripts/python scripts/evaluate_revisions.py        # diff P/R/F1 vs ground truth
.venv/Scripts/python -m pytest tests/                     # 403 tests
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

### Structured extraction (M4, offline by default)

```bash
.venv/Scripts/python scripts/extract_entities.py                     # both versions, deterministic
.venv/Scripts/python scripts/extract_entities.py --llm               # + mock LLM pass
.venv/Scripts/python scripts/extract_entities.py --reset             # clear registry first
.venv/Scripts/python scripts/extract_entities.py --json              # machine-readable
.venv/Scripts/python scripts/extract_entities.py --provider openrouter --llm   # real LLM (key needed)
.venv/Scripts/python scripts/query_entities.py --entities                        # registry counts
.venv/Scripts/python scripts/query_entities.py --entity C-02                     # one entity
.venv/Scripts/python scripts/query_entities.py --related C-10                    # facts touching C-10
.venv/Scripts/python scripts/query_entities.py --fact "component:C-10|provides|interface:IF-13"   # provenance trail
.venv/Scripts/python scripts/evaluate_extraction.py              # P/R/F1 vs ground truth
```

The extraction pipeline: **deterministic pass** (table handlers for the
component catalogue / port tables / signal dictionary / dependency overview,
plus prose title & provider/consumer patterns — no LLM) → **optional LLM
pass** (same evidence-ID contract as M3; unknown IDs rejected) →
**mechanical validation** (evidence resolution, reference/domain/range
checks, confidence floor) → **normalization + dedupe** (name→ID alias map,
`subject|predicate|object` dedupe keys) → **SQLite registry** (existing M1
typed entity tables + `extraction_facts` + audit events).

**Measured on this corpus** (`scripts/evaluate_extraction.py`): entities
**P=1.000 R=1.000 F1=1.000** and facts **P=1.000 R=1.000 F1=1.000** on
BOTH HLD versions (165 and 149 gold entities; 200 and 178 gold facts),
provenance accuracy **1.000**, zero validation issues, ~17 ms per version
deterministic end-to-end. Report: `data/evaluation/extraction_evaluation.json`.

## M4 extraction architecture

```
chunks (M2, provenance-carrying)
    │
    ├─► deterministic.py ── table handlers + prose patterns (D-023)
    │      3.1 catalogue -> components · 3.2.x port tables -> ports +
    │      provides/requires · 4.x titles -> interfaces + provider/consumer
    │      facts · 5 dictionary -> signals + carries · 6.1/6.2 ->
    │      dependencies + depends_on · 7.x -> flows + participates_in
    │      (owner attribution via the section-4 provider map — page-flowed
    │       tables are NOT owned by the nearest preceding title, D-012 lesson)
    │
    ├─► context.py ── evidence blocks [EVIDENCE E1..En] + trusted map (M4.6)
    │      └─ llm.py ── structured JSON {entities, facts} via M3 providers
    │            (mock default; openrouter/ollama via config; unknown
    │             evidence IDs rejected — never trusted provenance)
    │
    ▼
validator.py ── mechanical validation (M4.7, D-022)
    │   evidence resolution · reference existence · domain/range checks ·
    │   confidence floor · deterministic dedupe (best confidence wins)
    ▼
registry.py ── SQLite (M4.10/M4.11)
    │   M1 typed tables (components/interfaces/ports/signals/
    │   dependencies/functional_flows) + extraction_facts (uniform,
    │   dedupe-keyed) + AnalysisRun + append-only audit events
    ▼
query_entities.py ── queryable structured knowledge with full traceability:
    fact -> chunk -> document -> version -> section -> page
```

### Architecture Graph Explorer (M5, fully offline)

```bash
.venv/Scripts/python scripts/graph_explorer.py --version 1.0.0 --stats
.venv/Scripts/python scripts/graph_explorer.py --version 1.0.0 --related C-02
.venv/Scripts/python scripts/graph_explorer.py --version 1.0.0 --path C-02 IF-01
.venv/Scripts/python scripts/graph_explorer.py --version 1.0.0 --predicate requires
.venv/Scripts/python scripts/graph_explorer.py --version 1.0.0 --type component
.venv/Scripts/python scripts/graph_explorer.py --version 1.0.0 --confidence 0.95
.venv/Scripts/python scripts/graph_explorer.py --version 1.0.0 --node C-02 --depth 1
.venv/Scripts/python scripts/graph_explorer.py --version 1.0.0 --render          # pyvis HTML -> data/exports/
.venv/Scripts/python scripts/graph_explorer.py --version 1.0.0 --json           # node-link JSON -> data/graphs/
.venv/Scripts/python scripts/graph_explorer.py --version 1.0.0 --export-graphml
.venv/Scripts/python scripts/evaluate_graph.py                   # P/R/F1 vs ground truth, both versions
```

Example (`--stats --related C-02 --path C-02 IF-01` on v1.0.0):

```
== graph v1.0.0 ==
  nodes: 165  edges: 200  (build 46.2 ms)
  nodes by type: component=20, interface=25, port=57, signal=34, dependency=24, functional_flow=5
  edges by predicate: carries=34, depends_on=24, implements=57, participates_in=28, provides=25, requires=32
== related: component:C-02 (component) — 11 relationships
  component:C-02 -[depends_on]-> component:C-08   conf=0.95  ABC_HLD_v1.0.0.pdf s6.1 p14 chunk 7ca59d9a5ce9
  component:C-02 -[provides]-> interface:IF-03    conf=0.90  ABC_HLD_v1.0.0.pdf s4.3 p9  chunk 629161b96e98
  ...
== path: component:C-02 -> component:C-08 -> interface:IF-01
```

The graph is **derived, never authored** (D-026): nodes come from the M4
typed registry tables, edges 1:1 from `extraction_facts` rows keyed by
`fact_key` (a `MultiDiGraph`, so distinct facts never collapse, D-027).
Every edge carries the trusted provenance snapshot (document, version,
section, page range, chunk id, confidence, extractor) rendered into the
pyvis edge popup — hover any relationship to see exactly which document
page supports it (D-028). Graphs are strictly version-scoped
(`--version 1.0.0` vs `--version 1.1.0`, D-029).

**Measured** (`scripts/evaluate_graph.py`): nodes and edges
**P=R=F1=1.000 on both versions** (165/200 and 149/178 expected), version
isolation clean, provenance correctness **1.000**, registry↔graph 1:1
(0 missing, 0 extra, 0 duplicate fact keys), build ≈ 15–50 ms per version.
Report: `data/evaluation/graph_evaluation.json`.

### Architecture Findings (M6, fully offline, no LLM)

```bash
.venv/Scripts/python scripts/analyze_findings.py --version 1.0.0             # summary
.venv/Scripts/python scripts/analyze_findings.py --version 1.1.0 --persist   # store (idempotent)
.venv/Scripts/python scripts/analyze_findings.py --version 1.1.0 --json      # machine-readable
.venv/Scripts/python scripts/analyze_findings.py --version 1.1.0 --persist --reset
.venv/Scripts/python scripts/analyze_findings.py --version 1.1.0 --finding-type orphan_entity
.venv/Scripts/python scripts/evaluate_findings.py                            # P/R/F1 vs planted defects
.venv/Scripts/python scripts/evaluate_findings.py --json
```

Example (`--version 1.1.0`):

```
Version: 1.1.0
Entities analyzed: 149
Facts analyzed:    178
Findings: 1

By type:
  UNDEFINED_REFERENCE: 0
  DANGLING_REQUIRES: 0
  DUPLICATE_INTERFACE: 0
  CONFLICTING_PROVIDERS: 0
  ORPHAN_ENTITY: 1
  UNCONSUMED_SIGNAL: 0

[medium] M6-ORPHAN-ed409bb95a
  orphan_entity: Orphan entity component:C-05
  Entity component:C-05 ('SeatAdjustSWC') has degree 0 in the v1.1.0 architecture graph ...
  evidence: 1 item(s), source: ABC_HLD_v1.1.0.pdf 3.1 p4
```

Findings are **deterministically detected from structured architecture
facts** — six rule families over the M4 registry + M5 graph (undefined
references, dangling requires, duplicate interfaces, conflicting providers,
orphan entities, unconsumed signals; exact rules in D-032). Every finding
carries a deterministic ID (`M6-<TYPE>-<hash>`), trusted provenance copied
verbatim from registry metadata, and rule-based confidence. Persistence
reuses the M1 `AnalysisRun`/`Finding` tables: re-runs are idempotent and
preserve human review status (D-034). What a finding is NOT: a semantic
judgment — the LLM plays no role in detection, and severity/confidence are
rule tiers, not safety ratings.

**Measured** (`scripts/evaluate_findings.py`): v1.0.0 is a clean baseline
(0 findings); v1.1.0 detects the planted D4 orphan (C-05) with
**P = R = F1 = 1.000** on applicable gold. The other planted defects are
prose-vs-structure or cross-version and are reported as not-applicable
with reasons (D-035) — never silently skipped, never fabricated.

## M7 revision compare (fully offline, no LLM)

```bash
.venv/Scripts/python scripts/compare_revisions.py \
    --base-version 1.0.0 --target-version 1.1.0          # required pair
.venv/Scripts/python scripts/compare_revisions.py \
    --base-version 1.0.0 --target-version 1.1.0 --depth 2 --json
.venv/Scripts/python scripts/compare_revisions.py \
    --base-version 1.0.0 --target-version 1.1.0 --persist   # cache run
.venv/Scripts/python scripts/evaluate_revisions.py --save             # P/R/F1
```

Entity diff runs on canonical M4 keys, relationship diff on fact-triple
identity, both carrying trusted provenance from the registry (D-037).
Impact analysis is a deterministic BFS over the version graphs with
real-edge paths, controlled categories, and configurable depth (D-038);
every impact path is mechanically re-verified against the graph (V8).
Changes are changes — findings (e.g. `stale_reference`) arise only from
explicit deterministic rules. Measured on the synthetic pair: 0 added /
16 removed / 1 renamed entities, 43 added / 65 removed relationships,
974 impacts at depth 1 — **P = R = F1 = 1.000** on every applicable gold
family, with D1 (removed dependency) and D8 (new consumer) detected and
prose-only defects reported not-applicable with reasons (D-040).

## M5 graph architecture

```
SQLite registry (M4: typed tables + extraction_facts)   <- source of truth
    ▼ builder.py (version-scoped SQL join, D-026)
MultiDiGraph
    nodes: component:C-02 ... (typed attrs, confidence, display name)
    edges: fact_key-keyed, predicate + trusted provenance dict (D-027/D-028)
    ▼ validation.py (mechanical)   analysis.py (degree/path/components)
    ▼ filtering.py (type/predicate/confidence/ego-depth, non-mutating)
    ├─► export.py ── node-link JSON (data/graphs/) + GraphML
    └─► visualization.py ── pyvis standalone HTML (data/exports/)
            inlined vis-network (no CDN), UTF-8, provenance edge popups
    ▼ GraphService (service.py) ── the typed facade for CLI/M6/M7/M8
```

## Repository layout

```
backend/
  dataset/       M0: source-of-truth model, PDF renderer, ground truth
  ingestion/     M1: parsing, cleaning, sections, tables, OCR hook, pipeline
  rag/           M2: chunker, embedder, benchmark, vector store, retriever,
                 indexing, evaluation
                 M3: lexical (BM25), hybrid (RRF), gate, context, citations,
                 copilot, llm/ (mock | openrouter | ollama)
  extraction/    M4: schema, deterministic extractor, LLM extraction,
                 validator, registry persistence, service, evaluation
  graph/         M5: MultiDiGraph builder, validation, analysis, filtering,
                 JSON/GraphML export, pyvis rendering, service, evaluation
  findings/      M6: finding model, six deterministic detectors, engine,
                 validator (V1-V10), idempotent persistence, evaluation
  diff/          M7: two-version context, entity/relationship diff, impact
                 analysis, revision findings, validation (V1-V9),
                 CompareRun persistence, evaluation
  storage/       SQLite schema, sessions, audit log
  services/      application layer (UI-agnostic business logic)
app/             Streamlit UI (M8)
scripts/         dataset generation, ingestion, indexing, evaluation,
                 copilot CLI, gate calibration, extraction + registry CLIs,
                 graph explorer + graph evaluation,
                 findings analysis + findings evaluation,
                 revision compare + revision evaluation
tests/           pytest suite (403 tests green at M7; opt-in model tests)
docs/            decisions, status, architecture, evaluation, demo script
data/            generated artifacts (gitignored: processed/, vectors/,
                 evaluation/, db/, graphs/, exports/)
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
