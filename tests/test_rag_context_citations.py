"""M3 tests: grounded context builder + mechanical citation validator.

Covers: evidence formatting/ID assignment/metadata preservation/determinism
(context) and structured parsing, unknown-evidence rejection, missing
citations, declared-vs-cited mismatches, quote extraction and deterministic
citation rendering (validator). All pure logic, no I/O.
"""

from __future__ import annotations

import pytest

from backend.rag.citations import (Citation, parse_structured_response,
                                   render_citations_block,
                                   validate_response)
from backend.rag.context import build_context
from backend.rag.models import RetrievedChunk


# ---------------------------------------------------------------- helpers --

def _hit(chunk_id: str, text: str, section_no: str = "4.15",
         page_start: int = 10, page_end: int = 10) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id, text=text, distance=0.1, similarity=0.9,
        document_name="ABC_HLD_v1.0.0.pdf", version="1.0.0",
        section_no=section_no, section_title="VehicleSpeedIF",
        page_start=page_start, page_end=page_end, chunk_type="table",
        chunk_seq=0, token_count=50)


SPEED_TEXT = ("Table columns: Signal | Datatype | Unit\n"
              "VehicleSpeed | uint16 | km/h\n"
              "SpeedValidity | boolean | -")


# ---------------------------------------------------------------- context --

def test_context_assigns_rank_ordered_evidence_ids():
    chunks = [_hit("c1", "alpha text"), _hit("c2", "beta text"),
              _hit("c3", "gamma text")]
    ctx = build_context("q?", chunks)
    assert [b.evidence_id for b in ctx.blocks] == ["E1", "E2", "E3"]
    assert ctx.evidence_map["E1"].chunk_id == "c1"


def test_context_block_contains_full_provenance():
    ctx = build_context("Which signal?", [_hit("c1", SPEED_TEXT)])
    prompt = ctx.user_prompt
    assert "[EVIDENCE E1]" in prompt
    assert "Document: ABC_HLD_v1.0.0.pdf" in prompt
    assert "Version: 1.0.0" in prompt
    assert "Section: 4.15 VehicleSpeedIF" in prompt
    assert "Pages: 10" in prompt
    assert "VehicleSpeed | uint16 | km/h" in prompt
    assert "Question: Which signal?" in prompt


def test_context_max_evidence_and_truncation():
    chunks = [_hit(f"c{i}", "x" * 200) for i in range(8)]
    ctx = build_context("q?", chunks, max_evidence=3, max_block_chars=50)
    assert len(ctx.blocks) == 3
    assert "..." in ctx.blocks[0].text or len(ctx.blocks[0].text) <= 200


def test_context_deterministic():
    chunks = [_hit("c1", SPEED_TEXT), _hit("c2", "other")]
    p1 = build_context("q?", list(chunks)).user_prompt
    p2 = build_context("q?", list(chunks)).user_prompt
    assert p1 == p2


def test_context_system_prompt_has_grounding_rules():
    prompt = build_context("q?", [_hit("c1", "t")]).system_prompt
    assert "only" in prompt.lower()
    assert "insufficient_evidence" in prompt
    assert "never invent" in prompt.lower()
    assert "evidence_ids" in prompt


def test_context_empty_chunks():
    ctx = build_context("q?", [])
    assert ctx.blocks == []
    assert "Question: q?" in ctx.user_prompt


# --------------------------------------------------- structured parsing ----

def test_parse_valid_structured_response():
    raw = ('{"answer": "SpeedProviderSWC provides it. [E1]", '
           '"evidence_ids": ["E1"], "insufficient_evidence": false}')
    parsed = parse_structured_response(raw)
    assert parsed.parse_ok
    assert parsed.answer == "SpeedProviderSWC provides it. [E1]"
    assert parsed.evidence_ids == ["E1"]
    assert parsed.insufficient_evidence is False


def test_parse_tolerates_markdown_fences():
    raw = ('```json\n{"answer": "a [E2]", "evidence_ids": ["E2"], '
           '"insufficient_evidence": false}\n```')
    parsed = parse_structured_response(raw)
    assert parsed.parse_ok and parsed.answer == "a [E2]"


def test_parse_tolerates_surrounding_prose():
    raw = ('Sure! Here is the result:\n{"answer": "x [E1]", '
           '"evidence_ids": ["E1"], "insufficient_evidence": false} hope '
           'that helps')
    parsed = parse_structured_response(raw)
    assert parsed.parse_ok and parsed.answer == "x [E1]"


def test_parse_refusal_shape():
    raw = ('{"answer": "", "evidence_ids": [], '
           '"insufficient_evidence": true}')
    parsed = parse_structured_response(raw)
    assert parsed.parse_ok and parsed.declared_refusal


def test_parse_failures():
    for bad in ["", "no json at all", "[]", '{"answer": 1}',
                '{"answer": "a", "evidence_ids": "E1", '
                '"insufficient_evidence": false}',
                '{"answer": "a", "evidence_ids": [], "insufficient_evidence": '
                '"no"}']:
        parsed = parse_structured_response(bad)
        assert not parsed.parse_ok, bad


def test_parse_missing_evidence_ids_defaults_empty_and_gated():
    """Missing evidence_ids tolerates to []; a non-refusal answer then
    fails validation via the no-citations gate (safe degradation)."""
    parsed = parse_structured_response(
        '{"answer": "a", "insufficient_evidence": false}')
    assert parsed.parse_ok and parsed.evidence_ids == []
    ctx = build_context("q?", [_hit("c1", SPEED_TEXT)])
    result = validate_response(parsed, ctx)
    assert not result.ok
    assert any(i.kind == "no_citations" for i in result.issues)


# ------------------------------------------------------------- validation --

def _ctx_with_chunks(chunks):
    return build_context("q?", chunks)


def test_validate_accepts_grounded_answer():
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT)])
    parsed = parse_structured_response(
        '{"answer": "VehicleSpeed is uint16 km/h [E1].", '
        '"evidence_ids": ["E1"], "insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    assert result.ok
    assert len(result.citations) == 1
    c = result.citations[0]
    assert c.chunk_id == "c1"
    assert c.document_name == "ABC_HLD_v1.0.0.pdf"
    assert c.version == "1.0.0"
    assert c.section_no == "4.15"
    assert c.page_start == 10 and c.page_end == 10


def test_validate_rejects_unknown_evidence_id():
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT)])
    parsed = parse_structured_response(
        '{"answer": "Claim [E9].", "evidence_ids": ["E9"], '
        '"insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    assert not result.ok                       # no surviving citations
    assert any(i.kind == "unknown_evidence_id" for i in result.issues)
    assert "E9" in result.rejected_ids[0]


def test_validate_fails_mixed_valid_and_unknown_inline_citation():
    """Regression: one valid citation must NEVER rescue a fabricated one.
    'Some claim [E999] [E1]' used to pass validation because the surviving
    [E1] satisfied the >=1-citation gate; any unknown inline ID now hard-
    fails the whole response."""
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT)])
    parsed = parse_structured_response(
        '{"answer": "Some claim [E999] [E1].", '
        '"evidence_ids": ["E1"], "insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    assert not result.ok
    assert result.citations == []              # nothing is salvaged
    assert any(i.kind == "unknown_evidence_id" and "E999" in i.detail
               for i in result.issues)


def test_validate_fails_unknown_declared_id_with_valid_inline():
    """A fabricated evidence_ids entry fails the response even when the
    inline citations are all valid."""
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT)])
    parsed = parse_structured_response(
        '{"answer": "Grounded claim [E1].", '
        '"evidence_ids": ["E1", "E999"], "insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    assert not result.ok
    assert result.citations == []
    assert any(i.kind == "unknown_evidence_id" and "E999" in i.detail
               for i in result.issues)


def test_validate_rejects_fabricated_page_via_unknown_id():
    """An LLM cannot smuggle in provenance: any ID outside the evidence map
    is rejected regardless of what page/section it claims."""
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT, page_start=10)])
    parsed = parse_structured_response(
        '{"answer": "See page 42 [E7].", "evidence_ids": ["E7"], '
        '"insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    assert not result.ok
    assert result.citations == []


def test_validate_requires_citation_for_grounded_answer():
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT)])
    parsed = parse_structured_response(
        '{"answer": "A statement with no citations.", "evidence_ids": [], '
        '"insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    assert not result.ok
    assert any(i.kind == "no_citations" for i in result.issues)


def test_validate_flags_uncited_declared_id():
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT), _hit("c2", "other text")])
    parsed = parse_structured_response(
        '{"answer": "Statement [E1].", "evidence_ids": ["E1", "E2"], '
        '"insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    assert result.ok
    assert any(i.kind == "uncited_declared_id" and "E2" in i.detail
               for i in result.issues)


def test_validate_refusal_short_circuits():
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT)])
    parsed = parse_structured_response(
        '{"answer": "", "evidence_ids": [], "insufficient_evidence": true}')
    result = validate_response(parsed, ctx)
    assert result.ok and result.citations == [] and result.answer == ""


def test_validate_empty_answer_without_refusal_fails():
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT)])
    parsed = parse_structured_response(
        '{"answer": "", "evidence_ids": [], "insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    assert not result.ok
    assert any(i.kind == "empty_answer" for i in result.issues)


def test_validate_citation_metadata_comes_from_trusted_chunk():
    """Even if the model wrote a wrong-looking section next to a VALID id,
    the rendered citation uses the chunk's metadata (and only it)."""
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT, section_no="5")])
    parsed = parse_structured_response(
        '{"answer": "Per section 99, VehicleSpeed is uint16 [E1].", '
        '"evidence_ids": ["E1"], "insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    assert result.ok
    assert result.citations[0].section_no == "5"        # trusted, not LLM's


def test_validate_deduplicates_repeated_citations():
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT)])
    parsed = parse_structured_response(
        '{"answer": "A [E1] and again [E1].", "evidence_ids": ["E1"], '
        '"insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    assert result.ok and len(result.citations) == 1


def test_quote_extraction_finds_supporting_text():
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT)])
    parsed = parse_structured_response(
        '{"answer": "VehicleSpeed is uint16 km/h [E1].", '
        '"evidence_ids": ["E1"], "insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    assert "VehicleSpeed" in result.citations[0].quote


def test_render_citations_block_deterministic():
    ctx = _ctx_with_chunks([_hit("c1", SPEED_TEXT)])
    parsed = parse_structured_response(
        '{"answer": "x [E1]", "evidence_ids": ["E1"], '
        '"insufficient_evidence": false}')
    result = validate_response(parsed, ctx)
    block1 = render_citations_block(result.citations)
    block2 = render_citations_block(result.citations)
    assert block1 == block2
    assert block1.startswith("Sources:")
    assert "[1] ABC_HLD_v1.0.0.pdf v1.0.0" in block1
    assert "Section 4.15 VehicleSpeedIF - Page(s) 10" in block1
    assert "chunk c1" in block1


def test_citation_pages_csv_multi_page():
    c = Citation(evidence_id="E1", chunk_id="c", document_name="D",
                 version="1.0.0", section_no="1", section_title="T",
                 page_start=3, page_end=5, quote="", char_span=None)
    assert c.pages_csv == "3-5"
    c2 = Citation(evidence_id="E1", chunk_id="c", document_name="D",
                  version="1.0.0", section_no="1", section_title="T",
                  page_start=3, page_end=3, quote="", char_span=None)
    assert c2.pages_csv == "3"
