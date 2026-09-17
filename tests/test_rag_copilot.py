"""M3 tests: evidence gate + RAGCopilot orchestration + mocked E2E.

Covers: gate decisions per signal, calibration helper determinism, and the
copilot's four structured outcomes (answered / insufficient_evidence /
provider_failure / validation_failure) plus the full mocked pipeline on the
session corpus. All offline and deterministic.
"""

from __future__ import annotations

import json

import pytest

from backend.rag.copilot import RAGCopilot
from backend.rag.evaluation import load_qa_pairs
from backend.rag.embedder import HashingEmbedder
from backend.rag.gate import (assess_evidence, build_gate_questions,
                              evaluate_gate)
from backend.rag.hybrid import get_hybrid_service
from backend.rag.llm.factory import get_llm_provider
from backend.rag.llm.mock import MockLLMProvider
from backend.rag.models import RetrievalResult
from backend.rag.retriever import RetrievalService


# ------------------------------------------------------------------ gate ---

def _result_of(service, question: str, top_k: int = 5) -> RetrievalResult:
    return service.retrieve(question, top_k=top_k)


def test_gate_passes_clear_answerable_question(rag_store, processed_jsons):
    service = get_hybrid_service(embedder=HashingEmbedder(), store=rag_store)
    decision = assess_evidence(_result_of(service, "VehicleSpeed signal"))
    assert decision.passed, decision.reason
    assert decision.best_lexical_score is not None
    assert decision.query_coverage is not None and decision.query_coverage > 0.3


def test_gate_refuses_out_of_corpus_question(rag_store):
    """Zero lexical hits -> refused regardless of dense similarity."""
    service = get_hybrid_service(embedder=HashingEmbedder(), store=rag_store)
    decision = assess_evidence(
        _result_of(service, "What is the brake pressure of the front axle?"))
    assert not decision.passed
    assert "lexical" in decision.reason


def test_gate_dense_mode_skips_lexical_checks(rag_store, processed_jsons):
    """M2 dense-only compatibility: no lexical view -> checks skipped."""
    service = get_hybrid_service(embedder=HashingEmbedder(), store=rag_store)
    res = service.retrieve("brake pressure axle", top_k=5, mode="dense")
    decision = assess_evidence(res, min_score=0.0)
    assert decision.passed
    assert decision.best_lexical_score is None
    assert decision.query_coverage is None


def test_gate_min_hits_zero_chunks(rag_store):
    service = get_hybrid_service(embedder=HashingEmbedder(), store=rag_store)
    res = service.retrieve("vehicle speed", top_k=5)
    res.chunks = []
    decision = assess_evidence(res, min_hits=1)
    assert not decision.passed
    assert "too few" in decision.reason


def test_gate_min_score_threshold(rag_store):
    """min_score compares bounded similarity (0..1); an impossible
    threshold must refuse even strongly matched questions."""
    service = get_hybrid_service(embedder=HashingEmbedder(), store=rag_store)
    res = service.retrieve("VehicleSpeed signal", top_k=5)
    decision = assess_evidence(res, min_score=0.99)
    assert not decision.passed
    assert "best evidence score" in decision.reason


def test_build_gate_questions_shape():
    questions = build_gate_questions()
    assert len(questions) == 34
    assert sum(1 for q in questions if q["unanswerable"]) == 4
    assert all(set(q) >= {"question", "unanswerable", "id"} for q in questions)


def test_gate_calibration_deterministic(rag_store):
    service = get_hybrid_service(embedder=HashingEmbedder(), store=rag_store)
    questions = build_gate_questions()
    small = [q for q in questions if q["id"] in
             {"QA-01", "QA-02", "QA-U1", "QA-U2"}]
    grid1 = evaluate_gate(small, service, min_scores=[0.10],
                          min_lexicals=[0.25], min_coverages=[0.30])
    grid2 = evaluate_gate(small, service, min_scores=[0.10],
                          min_lexicals=[0.25], min_coverages=[0.30])
    assert grid1 == grid2
    row = grid1["grid"][0]
    assert row["false_refusals"] == 0
    assert row["missed_refusals"] == 0     # U1 lexical-zero, U2 coverage-low


# ---------------------------------------------------------------- copilot --

@pytest.fixture()
def copilot(rag_store) -> RAGCopilot:
    return RAGCopilot(retriever=get_hybrid_service(
        embedder=HashingEmbedder(), store=rag_store),
        llm=MockLLMProvider())


def test_copilot_answers_with_citations(copilot):
    answer = copilot.ask("Which component provides the VehicleSpeed signal?")
    assert answer.status == "answered"
    assert answer.answer
    assert len(answer.citations) >= 1
    for c in answer.citations:
        assert c.document_name.startswith("ABC_HLD")
        assert c.section_no
        assert c.page_start >= 1
    assert answer.sources_block.startswith("Sources:")
    assert answer.llm["provider"] == "mock"
    assert answer.retrieval["mode"] == "hybrid"
    assert answer.timings_ms["total_ms"] > 0


def test_copilot_answer_dict_roundtrip(copilot):
    answer = copilot.ask("Which interface does WindowLiftSWC provide?")
    d = answer.to_dict()
    assert d["status"] == "answered"
    assert d["citations"] and d["retrieval"]["chunk_ids"]
    json.dumps(d)      # must be JSON-serializable


def test_copilot_refuses_out_of_corpus_question(copilot):
    answer = copilot.ask("What is the brake pressure of the front axle?")
    assert answer.status == "insufficient_evidence"
    assert answer.answer == ""
    assert answer.citations == []
    assert "gate" in answer.detail or "lexical" in answer.detail


def test_copilot_provider_failure_is_structured(rag_store):
    copilot = RAGCopilot(retriever=get_hybrid_service(
        embedder=HashingEmbedder(), store=rag_store),
        llm=MockLLMProvider(fail_with="outage"))
    answer = copilot.ask("Which component provides the VehicleSpeed signal?")
    assert answer.status == "provider_failure"
    assert "outage" in answer.detail
    assert answer.retrieval["chunk_ids"]          # retrieval metadata kept


def test_copilot_llm_refusal_is_structured(rag_store):
    copilot = RAGCopilot(retriever=get_hybrid_service(
        embedder=HashingEmbedder(), store=rag_store),
        llm=MockLLMProvider(force_refusal=True))
    answer = copilot.ask("Which component provides the VehicleSpeed signal?")
    assert answer.status == "insufficient_evidence"
    assert "LLM judged evidence insufficient" in answer.detail


def test_copilot_malformed_llm_json_is_validation_failure(rag_store,
                                                          monkeypatch):
    class BadProvider(MockLLMProvider):
        def generate(self, system_prompt, user_prompt):
            from backend.rag.llm.base import LLMResponse
            return LLMResponse(text="this is not json", provider="mock",
                               model="bad", latency_ms=0.0)

    copilot = RAGCopilot(retriever=get_hybrid_service(
        embedder=HashingEmbedder(), store=rag_store), llm=BadProvider())
    answer = copilot.ask("Which component provides the VehicleSpeed signal?")
    assert answer.status == "validation_failure"


def test_copilot_fabricated_citation_is_rejected(rag_store):
    """LLM cites an evidence ID that was never supplied -> no answer."""
    class Fabricator(MockLLMProvider):
        def generate(self, system_prompt, user_prompt):
            from backend.rag.llm.base import LLMResponse
            payload = {"answer": "Invented claim [E99].",
                       "evidence_ids": ["E99"],
                       "insufficient_evidence": False}
            return LLMResponse(text=json.dumps(payload), provider="mock",
                               model="fabricator", latency_ms=0.0)

    copilot = RAGCopilot(retriever=get_hybrid_service(
        embedder=HashingEmbedder(), store=rag_store), llm=Fabricator())
    answer = copilot.ask("Which component provides the VehicleSpeed signal?")
    assert answer.status == "validation_failure"
    assert answer.citations == []


def test_copilot_mixed_valid_and_fabricated_citation_fails(rag_store):
    """Regression: a valid citation next to a fabricated one must not slip
    through to 'answered' — the whole response is rejected."""
    class MixedCiter(MockLLMProvider):
        def generate(self, system_prompt, user_prompt):
            from backend.rag.llm.base import LLMResponse
            payload = {"answer": "Some claim [E999] [E1].",
                       "evidence_ids": ["E1"],
                       "insufficient_evidence": False}
            return LLMResponse(text=json.dumps(payload), provider="mock",
                               model="mixed-citer", latency_ms=0.0)

    copilot = RAGCopilot(retriever=get_hybrid_service(
        embedder=HashingEmbedder(), store=rag_store), llm=MixedCiter())
    answer = copilot.ask("Which component provides the VehicleSpeed signal?")
    assert answer.status == "validation_failure"
    assert answer.answer == ""                # answer never surfaces
    assert answer.citations == []
    assert any(i["kind"] == "unknown_evidence_id" and "E999" in i["detail"]
               for i in answer.issues)


def test_copilot_empty_question(rag_store):
    copilot = RAGCopilot(retriever=get_hybrid_service(
        embedder=HashingEmbedder(), store=rag_store), llm=MockLLMProvider())
    answer = copilot.ask("   ")
    assert answer.status == "validation_failure"


def test_copilot_version_filter_flows_through(copilot):
    answer = copilot.ask("Which component provides VehicleSpeed?",
                         filters={"version": "1.0.0"})
    assert answer.status == "answered"
    for c in answer.citations:
        assert c.version == "1.0.0"


def test_copilot_config_backed_defaults():
    from backend.config import (EVIDENCE_MIN_COVERAGE,
                                EVIDENCE_MIN_LEXICAL_SCORE)
    copilot = RAGCopilot(retriever=object(), llm=MockLLMProvider())
    assert copilot.min_lexical_score == EVIDENCE_MIN_LEXICAL_SCORE
    assert copilot.min_query_coverage == EVIDENCE_MIN_COVERAGE


def test_copilot_accepts_m2_dense_service(rag_store):
    """Backwards compatibility: the M2 RetrievalService works as retriever
    (dense mode; lexical-side gate checks auto-skip)."""
    copilot = RAGCopilot(retriever=RetrievalService(
        embedder=HashingEmbedder(), store=rag_store),
        llm=MockLLMProvider())
    answer = copilot.ask("Which component provides the VehicleSpeed signal?")
    assert answer.status == "answered"
    assert answer.retrieval["mode"] == "dense"


# -------------------------------------------------------------------- E2E ---

def test_end_to_end_answerable_set_via_copilot(rag_store):
    """Sample of gold QA questions must reach 'answered' with citations."""
    service = get_hybrid_service(embedder=HashingEmbedder(), store=rag_store)
    copilot = RAGCopilot(retriever=service, llm=MockLLMProvider())
    qa_sample = load_qa_pairs()[:8]
    answered = 0
    for qa in qa_sample:
        result = copilot.ask(qa["question"])
        if result.status == "answered":
            answered += 1
            assert result.citations
    assert answered >= 6, f"only {answered}/{len(qa_sample)} answered"


def test_end_to_end_unanswerable_split_across_layers(rag_store):
    """Honest two-layer defense behaviour with the OFFLINE mock (D-019):
    - the gate mechanically refuses U1 (zero lexical) and U2 (low coverage);
    - U3/U4 are topically adjacent, pass the gate, and the deterministic
      mock -- which by design cannot judge answerability -- answers them.
      That residual gap is exactly what a real LLM's structured refusal
      covers (proven by the forced-refusal test below); it is a documented
      limitation of the mock provider, not of the pipeline.
    """
    service = get_hybrid_service(embedder=HashingEmbedder(), store=rag_store)
    copilot = RAGCopilot(retriever=service, llm=MockLLMProvider())
    outcomes = {}
    for q in build_gate_questions():
        if not q["unanswerable"]:
            continue
        result = copilot.ask(q["question"])
        outcomes[q["id"]] = result
    assert outcomes["QA-U1"].status == "insufficient_evidence"
    assert "gate" in outcomes["QA-U1"].detail
    assert outcomes["QA-U2"].status == "insufficient_evidence"
    assert "gate" in outcomes["QA-U2"].detail
    for qid in ("QA-U3", "QA-U4"):
        # mock limitation: answers topically-adjacent negatives
        assert outcomes[qid].status == "answered"


def test_end_to_end_unanswerable_via_forced_llm_refusal(rag_store):
    """The LLM-layer refusal path produces the correct structured result
    for every negative that reaches it (models a real provider's judgment
    on topically-adjacent questions)."""
    service = get_hybrid_service(embedder=HashingEmbedder(), store=rag_store)
    copilot = RAGCopilot(retriever=service,
                         llm=MockLLMProvider(force_refusal=True))
    for q in build_gate_questions():
        if not q["unanswerable"]:
            continue
        result = copilot.ask(q["question"])
        assert result.status == "insufficient_evidence"
        if result.llm:
            # reached the LLM layer -> structured refusal was the cause
            assert "LLM judged evidence insufficient" in result.detail


def test_end_to_end_factory_provider_mock(rag_store):
    """The CLI's exact construction path works end-to-end with the mock."""
    from backend import config
    service = get_hybrid_service(embedder=HashingEmbedder(),
                                 store=rag_store)
    copilot = RAGCopilot(retriever=service, llm=get_llm_provider("mock"))
    result = copilot.ask("What signals are carried by the DoorStatusIF "
                         "interface?")
    assert result.status in {"answered", "insufficient_evidence"}
    assert result.llm.get("provider") == "mock"
