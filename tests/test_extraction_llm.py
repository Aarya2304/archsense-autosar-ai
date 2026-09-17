"""M4 tests: extraction context + LLM-assisted extraction (mock provider).

Covers: evidence-ID assignment/trusted map, structured parsing (valid,
malformed, invalid types/predicates/confidence, unknown evidence IDs),
provider-failure degradation, and the full service pipeline with the mock
extraction provider. All offline and deterministic.
"""

from __future__ import annotations

import json

import pytest

from backend.extraction.context import build_extraction_context
from backend.extraction.llm import (MockExtractionProvider, parse_llm_extraction,
                                    run_llm_extraction)
from backend.extraction.models import Source
from backend.rag.chunker import chunk_processed_json
from backend.rag.llm.base import LLMError, LLMResponse, UsageInfo


@pytest.fixture(scope="module")
def v1_chunks():
    return chunk_processed_json("data/processed/ABC_HLD_v1.0.0__processed.json")


@pytest.fixture(scope="module")
def v1_ctx(v1_chunks):
    return build_extraction_context(v1_chunks[:20])


# ----------------------------------------------------------------- context --

def test_context_assigns_evidence_ids_in_order(v1_chunks):
    ctx = build_extraction_context(v1_chunks[:5])
    assert [b.evidence_id for b in ctx.blocks] == ["E1", "E2", "E3", "E4", "E5"]
    assert ctx.evidence_map["E1"].chunk_id == v1_chunks[0].chunk_id


def test_context_provenance_is_trusted_metadata(v1_chunks):
    ctx = build_extraction_context(v1_chunks[:3])
    src = ctx.evidence_map["E2"]
    assert src.document_name == v1_chunks[1].document_name
    assert src.version == v1_chunks[1].version
    assert src.page_start == v1_chunks[1].page_start
    assert src.chunk_id == v1_chunks[1].chunk_id


def test_context_prompt_contains_evidence_blocks(v1_ctx):
    prompt = ctx_user = v1_ctx.user_prompt()
    assert "[EVIDENCE E1]" in prompt
    assert "Document: ABC_HLD_v1.0.0.pdf" in prompt
    assert "never output document names" in \
        v1_ctx.system_prompt.lower()


def test_context_deterministic(v1_chunks):
    p1 = build_extraction_context(v1_chunks[:5]).user_prompt()
    p2 = build_extraction_context(v1_chunks[:5]).user_prompt()
    assert p1 == p2


# ------------------------------------------------------------------ parser --

def _resp(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="mock", model="m", latency_ms=1.0,
                       usage=UsageInfo(1, 1, 2))


def test_parse_valid_llm_extraction(v1_ctx):
    payload = {
        "entities": [{"type": "component", "name": "C-08",
                      "attributes": {"id": "C-08"}, "evidence_id": "E1"}],
        "facts": [{"subject": "component:C-08", "predicate": "provides",
                   "object": "interface:IF-02", "evidence_id": "E1"}],
    }
    result = parse_llm_extraction(_resp(json.dumps(payload)), v1_ctx)
    assert result.ok
    assert result.entities[0].key == "component:C-08"
    assert result.facts[0].predicate.value == "provides"
    assert result.entities[0].evidence.evidence_id == "E1"
    # provenance stayed unresolved until the validator (LLM gave only an ID)
    assert result.entities[0].evidence.source is None


def test_parse_tolerates_fences_and_prose(v1_ctx):
    raw = ('```json\n{"entities": [], "facts": []}\n```\nDone!')
    result = parse_llm_extraction(_resp(raw), v1_ctx)
    assert result.ok and not result.entities and not result.facts


def test_parse_malformed_json(v1_ctx):
    result = parse_llm_extraction(_resp("not json at all"), v1_ctx)
    assert not result.parse_ok
    assert "no parseable JSON" in result.parse_error


def test_parse_unknown_evidence_id_rejected(v1_ctx):
    payload = {"entities": [{"type": "component", "name": "C-08",
                             "evidence_id": "E999"}], "facts": []}
    result = parse_llm_extraction(_resp(json.dumps(payload)), v1_ctx)
    assert not result.ok
    assert any(i["kind"] == "unknown_evidence_id" for i in result.issues)
    assert result.entities == []          # never silently accepted


def test_parse_invalid_entity_type(v1_ctx):
    payload = {"entities": [{"type": "starship", "name": "X",
                             "evidence_id": "E1"}], "facts": []}
    result = parse_llm_extraction(_resp(json.dumps(payload)), v1_ctx)
    assert any(i["kind"] == "invalid_entity_type" for i in result.issues)


def test_parse_invalid_predicate(v1_ctx):
    payload = {"entities": [], "facts": [
        {"subject": "component:C-08", "predicate": "explodes",
         "object": "interface:IF-02", "evidence_id": "E1"}]}
    result = parse_llm_extraction(_resp(json.dumps(payload)), v1_ctx)
    assert any(i["kind"] == "invalid_predicate" for i in result.issues)


def test_parse_invalid_confidence_recorded(v1_ctx):
    payload = {"entities": [{"type": "component", "name": "C-08",
                             "evidence_id": "E1", "confidence": 5.0}],
               "facts": []}
    result = parse_llm_extraction(_resp(json.dumps(payload)), v1_ctx)
    assert any(i["kind"] == "construction_error" for i in result.issues)


# ------------------------------------------------------------------- mock ----

def test_mock_provider_deterministic_output(v1_ctx):
    p1 = run_llm_extraction(v1_ctx, MockExtractionProvider())
    p2 = run_llm_extraction(v1_ctx, MockExtractionProvider())
    assert p1.ok and p2.ok
    assert [(e.key, f.dedupe_key if hasattr(e, "dedupe_key") else "")
            for e in p1.entities] == \
        [(e.key, "") for e in p2.entities]
    assert {(f.subject, f.predicate.value, f.object) for f in p1.facts} == \
        {(f.subject, f.predicate.value, f.object) for f in p2.facts}


def test_mock_provider_failure_degrades(v1_ctx):
    result = run_llm_extraction(v1_ctx,
                                MockExtractionProvider(fail_with="outage"))
    assert not result.parse_ok
    assert "provider failure: outage" in result.parse_error
    assert result.entities == [] and result.facts == []


def test_mock_provider_malformed_degrades(v1_ctx):
    result = run_llm_extraction(v1_ctx, MockExtractionProvider(malformed=True))
    assert not result.parse_ok


def test_mock_provider_unknown_evidence_flag(v1_ctx):
    result = run_llm_extraction(
        v1_ctx, MockExtractionProvider(cite_unknown_evidence=True))
    assert any(i["kind"] == "unknown_evidence_id" for i in result.issues)


# ------------------------------------------------- service with mock LLM ----

def test_service_llm_path_offline(v1_chunks):
    from backend.extraction.service import ExtractionService
    svc = ExtractionService(llm_provider=MockExtractionProvider(),
                            use_llm=True)
    res = svc.extract_from_chunks(v1_chunks, document_name="t",
                                  version="1.0.0")
    assert res.status in {"completed", "completed_with_issues"}
    assert res.stats["llm"].get("provider") == "mock"
    assert res.timings_ms["llm_ms"] >= 0
    # deterministic pass still ran and produced gold-quality output
    assert res.timings_ms["deterministic_ms"] > 0


def test_service_llm_failure_keeps_deterministic_output(v1_chunks):
    from backend.extraction.service import ExtractionService
    svc = ExtractionService(
        llm_provider=MockExtractionProvider(fail_with="down"), use_llm=True)
    res = svc.extract_from_chunks(v1_chunks, document_name="t",
                                  version="1.0.0")
    assert res.status == "completed_with_issues"
    keys = {e.key for e in res.entities}
    assert "component:C-08" in keys          # deterministic fallback intact
    assert any("provider failure" in str(i) for i in res.issues)
