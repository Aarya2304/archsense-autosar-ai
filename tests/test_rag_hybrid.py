"""M3 tests: lexical BM25 + RRF hybrid retrieval.

Covers: BM25 ranking/determinism/empty queries, RRF fusion math (overlap,
single-list presence, empty lists, tie-breaks, metadata preservation), and
the HybridRetrievalService modes + filters. All fast and deterministic.
"""

from __future__ import annotations

import pytest

from backend.rag.embedder import HashingEmbedder
from backend.rag.hybrid import HybridRetrievalService, rrf_fuse
from backend.rag.lexical import LexicalRetriever, tokenize
from backend.rag.models import RetrievedChunk
from backend.rag.retriever import RetrievalService


# ---------------------------------------------------------------- helpers --

def _hit(chunk_id: str, text: str = "text", section_no: str = "1",
         doc: str = "D.pdf", **extra) -> RetrievedChunk:
    base = dict(
        chunk_id=chunk_id, text=text, distance=0.2, similarity=0.8,
        document_name=doc, version="1.0.0", section_no=section_no,
        section_title="Title", page_start=1, page_end=1, chunk_type="prose",
        chunk_seq=0, token_count=10)
    base.update(extra)
    return RetrievedChunk(**base)


# ------------------------------------------------------------------ BM25 ---

def test_tokenize_strips_stopwords_and_lowercases():
    assert tokenize("The VehicleSpeed Signal!") == ["vehiclespeed",
                                                    "signal"]


def test_lexical_ranks_matching_doc_first():
    docs = [_hit("a", "DoorStatusIF carries DoorStatus and ChildLockActive"),
            _hit("b", "VehicleSpeedIF carries VehicleSpeed km/h"),
            _hit("c", "Revision history of the document")]
    hits = LexicalRetriever().build(docs).retrieve("VehicleSpeed", top_k=3)
    # Only doc b contains the term; zero-score docs are excluded.
    assert [h.chunk_id for h in hits] == ["b"]
    assert hits[0].lexical_score > 0
    assert hits[0].lexical_rank == 1
    assert hits[0].sources == "lexical"


def test_lexical_multi_term_scoring():
    docs = [_hit("a", "VehicleSpeed signal filtered by SpeedProviderSWC"),
            _hit("b", "Signal dictionary lists every signal with datatype")]
    hits = LexicalRetriever().build(docs).retrieve("VehicleSpeed signal",
                                                   top_k=2)
    # a matches both terms, b only one -> a ranks first
    assert [h.chunk_id for h in hits] == ["a", "b"]
    assert [h.lexical_rank for h in hits] == [1, 2]


def test_lexical_provenance_preserved():
    docs = [_hit("a", "NvBlock interface for seat memory", section_no="4.2",
                 page_start=7, page_end=8)]
    hits = LexicalRetriever().build(docs).retrieve("NvBlock", top_k=1)
    assert hits[0].section_no == "4.2"
    assert (hits[0].page_start, hits[0].page_end) == (7, 8)


def test_lexical_no_match_returns_empty():
    docs = [_hit("a", "body control architecture")]
    hits = LexicalRetriever().build(docs).retrieve("airbag deployment",
                                                   top_k=5)
    assert hits == []


def test_lexical_empty_index_and_query():
    lr = LexicalRetriever()
    assert lr.retrieve("anything", top_k=5) == []
    lr.build([_hit("a", "vehicle speed")])
    assert lr.retrieve("", top_k=5) == []


def test_lexical_deterministic_ordering():
    docs = [_hit(f"d{i}", "same identical text") for i in range(5)]
    h1 = LexicalRetriever().build(list(docs)).retrieve("same text", top_k=5)
    h2 = LexicalRetriever().build(list(reversed(docs))).retrieve(
        "same text", top_k=5)
    assert [h.chunk_id for h in h1] == [h.chunk_id for h in h2]


# ------------------------------------------------------------------- RRF ---

def test_rrf_fuses_overlapping_results():
    fused = rrf_fuse([[_hit("a"), _hit("b")], [_hit("b"), _hit("a")]],
                     rrf_k=60, top_k=5)
    ids = [c.chunk_id for c in fused]
    assert set(ids) == {"a", "b"}
    by_id = {c.chunk_id: c for c in fused}
    # both documents have identical rank multisets -> equal fused scores,
    # deterministic tie-break by chunk id
    assert by_id["b"].rrf_score == pytest.approx(by_id["a"].rrf_score)
    assert ids == sorted(ids)  # tie-break order is stable


def test_rrf_document_in_one_list_still_contributes():
    fused = rrf_fuse([[_hit("a")], [_hit("b")]], rrf_k=60, top_k=5)
    assert {c.chunk_id for c in fused} == {"a", "b"}
    by_id = {c.chunk_id: c for c in fused}
    assert by_id["a"].sources == "lexical"
    assert by_id["b"].sources == "dense"


def test_rrf_double_contribution_beats_single():
    fused = rrf_fuse([[_hit("x"), _hit("a")], [_hit("x"), _hit("b")]],
                     rrf_k=60, top_k=5)
    assert fused[0].chunk_id == "x"
    assert fused[0].sources == "dense+lexical"
    assert fused[0].rrf_score > fused[1].rrf_score


def test_rrf_empty_lists():
    assert rrf_fuse([], top_k=5) == []
    assert rrf_fuse([[], []], top_k=5) == []


def test_rrf_top_k_truncates():
    fused = rrf_fuse([[_hit(f"c{i}") for i in range(10)] for _ in range(2)],
                     rrf_k=60, top_k=3)
    assert len(fused) == 3


def test_rrf_rrf_k_changes_scores_not_order():
    a = rrf_fuse([[_hit("x"), _hit("y")], [_hit("x")]], rrf_k=1, top_k=5)
    b = rrf_fuse([[_hit("x"), _hit("y")], [_hit("x")]], rrf_k=1000, top_k=5)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b] == ["x", "y"]
    assert a[0].rrf_score != b[0].rrf_score


def test_rrf_preserves_provenance_and_ranks():
    lex = [_hit("a", section_no="3.2.8", lexical_score=5.0, lexical_rank=1,
                sources="lexical")]
    den = [_hit("a", section_no="3.2.8", dense_rank=1)]
    fused = rrf_fuse([lex, den], rrf_k=60, top_k=5)
    c = fused[0]
    assert c.section_no == "3.2.8"
    assert c.lexical_rank == 1 and c.dense_rank == 1
    assert c.lexical_score == 5.0
    assert c.sources == "dense+lexical"


# --------------------------------------------------------------- service ---

@pytest.fixture()
def hybrid_service(rag_store) -> HybridRetrievalService:
    return HybridRetrievalService(
        dense=RetrievalService(embedder=HashingEmbedder(), store=rag_store))


def test_service_hybrid_default_mode(hybrid_service):
    res = hybrid_service.retrieve("VehicleSpeed signal", top_k=5)
    assert res.mode == "hybrid"
    assert res.chunks
    assert all(c.rrf_score is not None for c in res.chunks)


def test_service_dense_mode_matches_m2_service(hybrid_service):
    hybrid = hybrid_service.retrieve("VehicleSpeed signal", top_k=5,
                                     mode="dense")
    dense = hybrid_service.dense.retrieve("VehicleSpeed signal", top_k=5)
    assert [c.chunk_id for c in hybrid.chunks] == \
        [c.chunk_id for c in dense.chunks]
    assert hybrid.mode == "dense"
    assert all(c.rrf_score is None for c in hybrid.chunks)


def test_service_lexical_mode(hybrid_service):
    res = hybrid_service.retrieve("VehicleSpeed", top_k=3, mode="lexical")
    assert res.mode == "lexical"
    assert all(c.lexical_score is not None for c in res.chunks)


def test_service_unknown_mode_rejected(hybrid_service):
    with pytest.raises(ValueError):
        hybrid_service.retrieve("q", mode="bogus")


def test_service_determinism(hybrid_service):
    r1 = hybrid_service.retrieve("door lock flow", top_k=5)
    r2 = hybrid_service.retrieve("door lock flow", top_k=5)
    assert [c.chunk_id for c in r1.chunks] == \
        [c.chunk_id for c in r2.chunks]
    assert [c.rrf_score for c in r1.chunks] == \
        [c.rrf_score for c in r2.chunks]


def test_service_filters_apply_to_hybrid(hybrid_service):
    res = hybrid_service.retrieve("interface signals", top_k=5,
                                  filters={"version": "1.0.0"})
    assert res.chunks
    assert {c.version for c in res.chunks} == {"1.0.0"}


def test_service_unknown_filter_rejected(hybrid_service):
    with pytest.raises(ValueError):
        hybrid_service.retrieve("q", filters={"typo": 1})


def test_service_ensure_index_tracks_store(hybrid_service, rag_store):
    before = rag_store.count()
    hybrid_service.ensure_index()
    assert hybrid_service._lexical_n == before
    hybrid_service.ensure_index()          # no-op path
    assert hybrid_service._lexical_n == before
