"""Evidence quality gate (M3.11): decide when retrieval is strong enough
to attempt a grounded answer at all.

Three deterministic, pre-generation signals (no LLM involvement):

1. **Lexical-hit floor** — the corpus must contain at least one chunk whose
   *lexical* (BM25) match is non-trivial. In hybrid/lexical modes a
   query with zero lexical hits has no terminological anchor in the
   document; in dense-only mode this check is skipped (no lexical view).
2. **Query-term coverage of evidence** — IDF-weighted share of the query's
   content terms (question function words excluded, light stemming) that
   appear in the top-3 retrieved chunks. Questions about concepts missing
   from the corpus (``brake pressure``, ``OS task scheduling``) retrieve
   plausible-looking chunks via generic terms while the *content* terms are
   absent — the strongest available pre-generation refusal signal.
3. **Lexical/dense agreement** — share of the final hybrid list tagged
   ``dense+lexical``. Unanswerable questions typically rank differently in
   the two spaces (dense matches generic prose; lexical finds nothing).

Thresholds ship calibrated on this corpus (D-019, one-shot grid):
``min_lexical_score=0.25``, ``min_query_coverage=0.30`` mechanically refuse
QA-U1 (zero lexical match) and QA-U2 (low query coverage) at the cost of one
false refusal (QA-21); QA-U3/U4 are topically adjacent to corpus content and
pass the gate by design -- refusing those is the job of the second layer
(the LLM's structured ``insufficient_evidence`` flag plus citation
validation). Defense in depth, explicitly not completeness. Thresholds are
configuration (``backend.config``), not magic constants.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from backend.rag.models import RetrievalResult
from backend.rag.retriever import RetrievalService

# Question function words excluded from coverage (interrogatives and generic
# task verbs). Deliberately a fixed, documented set: it generalizes to new
# engineering questions, unlike a per-corpus tuned list.
_QUESTION_WORDS = frozenset({
    # interrogatives / auxiliaries
    "how", "what", "which", "who", "whom", "whose", "when", "where", "why",
    "does", "do", "did", "is", "are", "was", "were", "can", "could",
    "should", "shall", "will", "would", "many", "much", "according",
    # generic HLD-Q&A task verbs (near-zero discrimination value)
    "provide", "provides", "consume", "consumes", "require", "requires",
    "depend", "depends", "belong", "belongs", "include", "includes",
    "list", "order", "ids", "link", "connect", "connects", "communicate",
    "communicates", "carry", "carries", "use", "uses", "apply", "applies",
    "happen", "happens", "stored", "store", "using", "between", "into",
    "over", "this", "that", "these", "those", "their",
})

# Light deterministic stemming for morphological variants (provides ->
# provide). Suffix-stripping only, no dictionary, fully deterministic.
_PRE_STEMS = {
    "provide": "provide", "provides": "provide", "provided": "provide",
    "consume": "consume", "consumes": "consume", "consumed": "consume",
    "require": "require", "requires": "require", "required": "require",
    "depend": "depend", "depends": "depend", "depended": "depend",
}


def _stem_variants(term: str) -> set[str]:
    from backend.rag.lexical import stem_variants

    if term in _PRE_STEMS:
        return {_PRE_STEMS[term]}
    return stem_variants(term)


@dataclass
class GateDecision:
    """Outcome of the pre-generation evidence check."""

    passed: bool
    reason: str
    best_score: float                 # best bounded similarity in the list
    best_lexical_score: float | None  # best bounded BM25 similarity
    lexical_hits: int                 # chunks with lexical_score > 0
    query_coverage: float | None      # IDF-weighted term coverage of top-3
    missing_terms: list[str] = field(default_factory=list)
    agreement: float = 0.0            # "dense+lexical" share (hybrid only)
    n_chunks: int = 0
    thresholds: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "best_score": round(self.best_score, 4),
            "best_lexical_score": (round(self.best_lexical_score, 4)
                                   if self.best_lexical_score is not None
                                   else None),
            "lexical_hits": self.lexical_hits,
            "query_coverage": (round(self.query_coverage, 4)
                               if self.query_coverage is not None else None),
            "missing_terms": self.missing_terms,
            "agreement": round(self.agreement, 4),
            "n_chunks": self.n_chunks,
            "thresholds": {k: round(v, 4) for k, v in self.thresholds.items()},
        }


def assess_evidence(result: RetrievalResult,
                    min_score: float = 0.10,
                    min_hits: int = 1,
                    min_agreement: float = 0.0,
                    min_lexical_score: float = 0.25,
                    min_query_coverage: float = 0.30,
                    lexical=None) -> GateDecision:
    """Decide whether retrieval evidence justifies calling the LLM.

    All thresholds are plain keyword arguments so tests and the calibration
    harness can sweep them; production values come from ``backend.config``
    via ``RAGCopilot``. Dense-only results (``mode == "dense"``) skip the
    two lexical-side checks by design — the lexical view does not exist
    there — which keeps the M2 ``RetrievalService`` fully compatible.
    """
    thresholds = {"min_score": min_score, "min_hits": min_hits,
                  "min_agreement": min_agreement,
                  "min_lexical_score": min_lexical_score,
                  "min_query_coverage": min_query_coverage}
    kwargs = dict(thresholds=thresholds, agreement=0.0,
                  n_chunks=len(result.chunks))
    # best_score is computed once below and injected into kwargs so every
    # early-return branch carries it (GateDecision requires it).
    best_score = 0.0

    chunks = result.chunks
    if len(chunks) < min_hits:
        return GateDecision(
            passed=False,
            reason=f"too few chunks retrieved ({len(chunks)} < {min_hits})",
            best_score=0.0, best_lexical_score=None, lexical_hits=0,
            query_coverage=None, **kwargs)

    lexical_scores = [c.lexical_score for c in chunks
                      if c.lexical_score is not None]
    lexical_hits = sum(1 for s in lexical_scores if s > 0.0)
    best_lex = max(lexical_scores) if lexical_scores else None
    # best_score is the bounded similarity (BM25/(BM25+k1) in lexical modes,
    # cosine in dense mode) so min_score compares like with like across
    # modes and embedders; the raw BM25 score stays on lexical_score.
    best_score = max((c.similarity for c in chunks), default=0.0)
    kwargs["best_score"] = best_score

    if best_score < min_score:
        return GateDecision(
            passed=False,
            reason=f"best evidence score {best_score:.3f} < {min_score:.3f}",
            best_lexical_score=best_lex, lexical_hits=lexical_hits,
            query_coverage=None, **kwargs)

    if result.mode in ("hybrid", "lexical"):
        # --- lexical-hit floor -------------------------------------------
        if lexical_hits == 0 or (best_lex or 0.0) < min_lexical_score:
            return GateDecision(
                passed=False,
                reason=f"no strong lexical match (best {best_lex or 0.0:.3f}"
                       f" < {min_lexical_score:.3f}); question may be outside"
                       f" the corpus",
                best_lexical_score=best_lex, lexical_hits=lexical_hits,
                query_coverage=None, **kwargs)

        # --- query-term coverage of evidence (hybrid only needs it) ------
        coverage, missing = query_coverage(result, lexical=lexical)
        if coverage < min_query_coverage:
            return GateDecision(
                passed=False,
                reason=f"query terms not covered by evidence "
                       f"({coverage:.2f} < {min_query_coverage:.2f}; missing:"
                       f" {', '.join(missing[:6])})",
                best_lexical_score=best_lex, lexical_hits=lexical_hits,
                query_coverage=coverage, missing_terms=missing, **kwargs)
        cov_val, miss_val = coverage, missing
    else:
        cov_val, miss_val = None, []

    agreement = 0.0
    if result.mode == "hybrid" and chunks:
        agreement = sum(1 for c in chunks if c.sources == "dense+lexical") \
            / len(chunks)
        if agreement < min_agreement:
            return GateDecision(
                passed=False,
                reason=f"lexical/dense agreement {agreement:.2f} < "
                       f"{min_agreement:.2f}",
                best_lexical_score=best_lex, lexical_hits=lexical_hits,
                query_coverage=cov_val, missing_terms=miss_val, **kwargs)

    return GateDecision(
        passed=True, reason="evidence adequate",
        best_lexical_score=best_lex, lexical_hits=lexical_hits,
        query_coverage=cov_val, missing_terms=miss_val, **kwargs)


def query_coverage(result: RetrievalResult, top_n_chunks: int = 3,
                   lexical=None) -> tuple[float, list[str]]:
    """IDF-weighted share of the query's content terms found in evidence.

    Coverage is computed against the union of the top ``top_n_chunks``
    chunk texts. Term mass uses BM25 IDF from the lexical index; light
    stemming maps morphological variants. Returns ``(coverage, missing)``
    where ``missing`` lists uncovered content terms (great for UI/debug).
    """
    chunks = result.chunks
    if not chunks:
        return 0.0, []
    if lexical is None:
        # Attempt to borrow the caller's lexical index (hybrid service
        # passes it explicitly; standalone use builds a throwaway one).
        lexical = getattr(result, "_lexical_index", None)
    if lexical is None:
        from backend.rag.lexical import LexicalRetriever
        lexical = LexicalRetriever().build(chunks)

    n = len(lexical.chunks)
    df = lexical._df
    total_mass = 0.0
    covered_mass = 0.0
    missing: list[str] = []
    evidence_tokens: set[str] = set()
    for c in chunks[:top_n_chunks]:
        evidence_tokens |= set(lexical._tokenize_public(c.text))

    for term in sorted(set(lexical._tokenize_public(result.query))):
        if term in _QUESTION_WORDS:
            continue
        idf = math.log(1.0 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0)
                                                            + 0.5))
        total_mass += idf
        if any(v in evidence_tokens for v in _stem_variants(term)):
            covered_mass += idf
        else:
            missing.append(term)
    if total_mass <= 0.0:
        return 1.0, []
    return covered_mass / total_mass, missing


# ---------------------------------------------------------- calibration ---

def evaluate_gate(questions: list[dict], service,
                  min_scores: list[float] | None = None,
                  min_lexicals: list[float] | None = None,
                  min_coverages: list[float] | None = None,
                  top_k: int = 5) -> dict[str, Any]:
    """Sweep gate thresholds over answerable + unanswerable questions.

    ``questions``: dicts with ``question`` and ``unanswerable`` (bool).
    ``service``: hybrid retrieval service. Returns the full calibration
    grid: per threshold combination, false refusals on answerable questions
    and missed refusals on unanswerable ones. Run once, documented in
    D-019; thresholds are NOT repeatedly tuned against this grid.
    """
    from itertools import product

    min_scores = min_scores if min_scores is not None else [0.10]
    min_lexicals = min_lexicals if min_lexicals is not None else \
        [0.0, 0.15, 0.25, 0.35]
    min_coverages = min_coverages if min_coverages is not None else \
        [0.0, 0.15, 0.30, 0.45]

    # ensure the lexical index exists once (service reuse across the grid)
    if hasattr(service, "ensure_index"):
        service.ensure_index()

    rows: list[dict[str, Any]] = []
    for ms, ml, mc in product(min_scores, min_lexicals, min_coverages):
        false_refusals = 0
        missed_refusals = 0
        false_ids: list[str] = []
        missed_ids: list[str] = []
        for q in questions:
            res = service.retrieve(q["question"], top_k=top_k)
            decision = assess_evidence(res, min_score=ms,
                                       min_lexical_score=ml,
                                       min_query_coverage=mc,
                                       lexical=getattr(service, "_lexical",
                                                       None))
            if not q["unanswerable"] and not decision.passed:
                false_refusals += 1
                false_ids.append(q.get("id", "?"))
            if q["unanswerable"] and decision.passed:
                missed_refusals += 1
                missed_ids.append(q.get("id", "?"))
        rows.append({
            "min_score": ms, "min_lexical_score": ml,
            "min_query_coverage": mc,
            "false_refusals": false_refusals, "missed_refusals": missed_refusals,
            "false_refusal_ids": false_ids, "missed_refusal_ids": missed_ids,
            "n_answerable": sum(1 for q in questions if not q["unanswerable"]),
            "n_unanswerable": sum(1 for q in questions if q["unanswerable"]),
        })
    return {"top_k": top_k, "grid": rows}


def build_gate_questions(ground_truth_path=None) -> list[dict]:
    """Answerable (30 QA pairs) + unanswerable (4 negatives) question list."""
    import json
    from pathlib import Path

    from backend.config import GROUND_TRUTH_DIR
    from backend.rag.evaluation import load_qa_pairs

    path = Path(ground_truth_path) if ground_truth_path else \
        GROUND_TRUTH_DIR / "ground_truth.json"
    questions = [{"question": q["question"], "unanswerable": False,
                  "id": q["id"]} for q in load_qa_pairs(path)]
    gt = json.loads(path.read_text(encoding="utf-8"))
    questions.extend({"question": q["question"], "unanswerable": True,
                      "id": q["id"]}
                     for q in gt.get("unanswerable_questions") or [])
    return questions
