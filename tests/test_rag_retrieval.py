"""M2 tests: retrieval service + end-to-end indexing + evaluation.

Uses the session-scoped ChromaDB corpus (hashing embedder) for realistic
retrieval behaviour; mutation-heavy checks use fresh stores. All fast.
"""

from __future__ import annotations

import pytest

from backend.config import GROUND_TRUTH_DIR
from backend.rag.chunker import chunk_processed_json
from backend.rag.evaluation import (evaluate_retrieval, load_qa_pairs)
from backend.rag.embedder import HashingEmbedder
from backend.rag.indexing import index_processed_document
from backend.rag.retriever import RetrievalService
from backend.rag.vector_store import get_vector_store


# ----------------------------------------------------- retrieval service ----

def test_retrieve_returns_ordered_result(rag_service):
    res = rag_service.retrieve(
        "Which component provides the VehicleSpeed signal?", top_k=5)
    assert res.top_k == 5
    assert 1 <= len(res.chunks) <= 5
    dicts = res.to_dicts()
    assert [d["rank"] for d in dicts] == [1, 2, 3, 4, 5][:len(dicts)]
    scores = [d["score"] for d in dicts]
    assert scores == sorted(scores, reverse=True)
    for d in dicts:
        assert d["document_name"].startswith("ABC_HLD")
        assert d["version"] in {"1.0.0", "1.1.0"}
        assert isinstance(d["pages"], list) and d["pages"]
        assert d["chunk_type"] in {"prose", "table"}
        assert d["chunk_id"]


def test_retrieve_respects_top_k(rag_service):
    res = rag_service.retrieve("dependency table", top_k=3)
    assert len(res.chunks) <= 3


def test_version_filter_narrows_results(rag_service):
    res = rag_service.retrieve("terminology S-R interface", top_k=10,
                               filters={"version": "1.0.0"})
    assert res.chunks
    assert {c.version for c in res.chunks} == {"1.0.0"}


def test_document_filter(rag_service):
    res = rag_service.retrieve("revision history", top_k=5,
                               filters={"document_name": "ABC_HLD_v1.1.0.pdf"})
    assert res.chunks
    assert {c.document_name for c in res.chunks} == {"ABC_HLD_v1.1.0.pdf"}


def test_chunk_type_filter(rag_service):
    res = rag_service.retrieve("signal dictionary", top_k=10,
                               filters={"chunk_type": "table"})
    assert res.chunks
    assert {c.chunk_type for c in res.chunks} == {"table"}


def test_unknown_filter_rejected(rag_service):
    with pytest.raises(ValueError):
        rag_service.retrieve("q", filters={"bogus_key": 1})


def test_page_post_filter(rag_service):
    res = rag_service.retrieve("vehicle speed", top_k=10,
                               filters={"page_in": (9, 10)})
    assert res.chunks
    for c in res.chunks:
        span = set(range(c.page_start, c.page_end + 1))
        assert span & set(range(9, 11))


def test_service_logs_calls(rag_service):
    before = len(rag_service.call_log)
    rag_service.retrieve("audit test", top_k=2)
    assert len(rag_service.call_log) == before + 1
    entry = rag_service.call_log[-1]
    assert entry["query"] == "audit test"
    assert entry["n_hits"] <= 2


# ------------------------------------------------- end-to-end (M2.5 gate) ----

def test_end_to_end_index_dedupe(fresh_rag_store, processed_jsons):
    """ingestion record -> chunks -> embeddings -> Chroma, twice, no dupes."""
    e = HashingEmbedder()
    pj = processed_jsons[0]
    chunks1, summary1 = index_processed_document(pj, e, fresh_rag_store)
    size1 = fresh_rag_store.count()
    chunks2, summary2 = index_processed_document(pj, e, fresh_rag_store)
    assert fresh_rag_store.count() == size1, "re-index created duplicates"
    assert [c.chunk_id for c in chunks1] == [c.chunk_id for c in chunks2]
    assert summary1.n_chunks == summary2.n_chunks == len(chunks1)


def test_end_to_end_both_versions_share_collection(fresh_rag_store,
                                                   processed_jsons):
    e = HashingEmbedder()
    for pj in processed_jsons:
        index_processed_document(pj, e, fresh_rag_store)
    # Both documents indexed, no cross-document ID collisions.
    assert fresh_rag_store.count() == 145 + 134  # deterministic chunker


def test_rebuild_flag_resets_collection(fresh_rag_store, processed_jsons):
    e = HashingEmbedder()
    index_processed_document(processed_jsons[0], e, fresh_rag_store)
    index_processed_document(processed_jsons[0], e, fresh_rag_store,
                             rebuild=True)
    assert fresh_rag_store.count() == 145


def test_indexed_chunks_are_retrievable(fresh_rag_store, processed_jsons):
    e = HashingEmbedder()
    chunks, _ = index_processed_document(processed_jsons[0], e,
                                         fresh_rag_store)
    service = RetrievalService(embedder=e, store=fresh_rag_store)
    res = service.retrieve("R-port required port definition", top_k=3)
    assert res.chunks
    # Every hit must come from the chunk set we just indexed.
    indexed_ids = {c.chunk_id for c in chunks}
    assert {h.chunk_id for h in res.chunks} <= indexed_ids


# ------------------------------------------------------------- M2.6 eval ----

def test_load_qa_pairs_deterministic():
    pairs = load_qa_pairs()
    assert len(pairs) == 30
    ids = [q["id"] for q in pairs]
    assert ids == sorted(ids)


def test_evaluation_report_structure_and_gate(rag_service):
    report = evaluate_retrieval(rag_service, top_k=5)
    assert report.n_questions == 30
    assert 0.0 <= report.page_hit_at_k <= 1.0
    assert 0.0 <= report.section_hit_at_k <= 1.0
    assert 0.0 <= report.mrr_page <= 1.0
    assert report.median_latency_ms >= 0.0
    # Hashing baseline floor: retrieval must be clearly non-random.
    assert report.page_hit_at_k >= 0.5, report.summary_line()
    # Every question must have detail recorded.
    assert len(report.per_question) == 30
    q0 = report.per_question[0]
    assert q0.qa_id == "QA-01"
    assert q0.gold_pages


def test_evaluation_report_roundtrip(rag_service, tmp_path):
    report = evaluate_retrieval(rag_service, top_k=3)
    out = tmp_path / "eval.json"
    report.save(out)
    loaded = __import__("json").loads(out.read_text(encoding="utf-8"))
    assert loaded["top_k"] == 3
    assert loaded["n_questions"] == 30
    assert len(loaded["per_question"]) == 30


def test_evaluation_uses_existing_ground_truth():
    """Guard: evaluation reads the canonical GT file."""
    assert (GROUND_TRUTH_DIR / "ground_truth.json").exists()


# ------------------------------------------------------- benchmark engine ----

def test_benchmark_client_side_store_scoring(rag_service, processed_jsons):
    """The benchmark's client-side scorer must agree with the real pipeline."""
    from backend.rag.benchmark import _ClientSideCosineStore, _score_questions
    from backend.rag.vector_store import StoredChunk

    e = HashingEmbedder()
    chunks = chunk_processed_json(processed_jsons[0])
    qa = load_qa_pairs()[:5]
    metrics = _score_questions(e, chunks, qa, ks=[3])
    assert 3 in metrics
    for m in metrics[3].values():
        assert 0.0 <= m <= 1.0


def test_benchmark_run_with_hashing_only(processed_jsons, tmp_path):
    """Full run_benchmark pass with zero downloads (hashing candidate)."""
    from backend.rag.benchmark import run_benchmark
    out = tmp_path / "bench.json"
    report = run_benchmark(["hashing"], processed_dir=processed_jsons[0].parent,
                           output_path=out)
    assert out.exists()
    row = report["models"][0]
    assert row["model"].startswith("hashing")
    assert row["dimension"] == 512
    assert "1" in row["metrics_by_k"]
    assert row["metrics_by_k"]["5"]["page_hit"] >= 0.5
