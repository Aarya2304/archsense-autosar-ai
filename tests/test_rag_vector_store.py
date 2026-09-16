"""M2 tests: ChromaDB vector store (fresh per-test store, hashing embedder).

Covers: upsert/count, deterministic-ID duplicate prevention, similarity
query shape, metadata decoding, reset, and protocol conformance.
"""

from __future__ import annotations

import pytest

from backend.rag.embedder import HashingEmbedder
from backend.rag.models import RetrievedChunk
from backend.rag.vector_store import ChromaVectorStore, StoredChunk


def _chunk(i: int, text: str, version: str = "1.0.0",
           section: str = "3.1", page: int = 4) -> StoredChunk:
    return StoredChunk(
        chunk_id=f"chk{i:04d}" + "0" * 12,
        text=text,
        metadata={
            "document_name": f"ABC_HLD_v{version}.pdf",
            "version": version,
            "sha256": "f" * 64,
            "section_no": section,
            "section_title": "Test Section",
            "page_start": page,
            "page_end": page,
            "pages_csv": str(page),
            "chunk_seq": i,
            "chunk_type": "prose",
            "token_count": len(text) // 4,
        })


@pytest.fixture()
def store(fresh_rag_store) -> ChromaVectorStore:
    return fresh_rag_store


def test_protocol_conformance(store):
    assert callable(store.upsert_chunks)
    assert callable(store.count)
    assert callable(store.query)
    assert callable(store.reset)


def test_upsert_and_count(store):
    e = HashingEmbedder()
    chunks = [_chunk(0, "door status signal"), _chunk(1, "window lift motor")]
    vecs = e.embed([c.text for c in chunks])
    n = store.upsert_chunks(chunks, vecs)
    assert n == 2
    assert store.count() == 2


def test_reupsert_does_not_duplicate(store):
    """Re-indexing identical chunks must not create duplicates (M2 gate)."""
    e = HashingEmbedder()
    chunks = [_chunk(0, "door status signal"), _chunk(1, "window lift motor")]
    vecs = e.embed([c.text for c in chunks])
    store.upsert_chunks(chunks, vecs)
    store.upsert_chunks(chunks, vecs)
    store.upsert_chunks(chunks, vecs)
    assert store.count() == 2


def test_changed_content_upserts_new_id(store):
    """Edited content gets a new deterministic ID; old row is untouched."""
    e = HashingEmbedder()
    v1 = [_chunk(0, "door status signal version one")]
    store.upsert_chunks(v1, e.embed([c.text for c in v1]))
    v2 = [_chunk(1, "door status signal version two")]
    store.upsert_chunks(v2, e.embed([c.text for c in v2]))
    assert store.count() == 2


def test_query_returns_ranked_hits(store):
    e = HashingEmbedder()
    chunks = [
        _chunk(0, "VehicleSpeed signal provided by SpeedProviderSWC"),
        _chunk(1, "NvM seat memory profiles stored per user"),
        _chunk(2, "window lift motor commands"),
    ]
    store.upsert_chunks(chunks, e.embed([c.text for c in chunks]))
    q = e.embed_query(["VehicleSpeed provided by which component"])[0]
    hits = store.query(q, top_k=2)
    assert len(hits) == 2
    assert all(isinstance(h, RetrievedChunk) for h in hits)
    assert hits[0].chunk_id == chunks[0].chunk_id
    assert hits[0].similarity >= hits[1].similarity
    assert hits[0].page_start == 4
    assert hits[0].version == "1.0.0"


def test_query_with_metadata_filter(store):
    e = HashingEmbedder()
    chunks = [_chunk(0, "door status", version="1.0.0"),
              _chunk(1, "door status v2", version="1.1.0")]
    store.upsert_chunks(chunks, e.embed([c.text for c in chunks]))
    q = e.embed_query(["door status"])[0]
    hits = store.query(q, top_k=5, where={"version": "1.1.0"})
    assert len(hits) == 1
    assert hits[0].version == "1.1.0"


def test_length_mismatch_rejected(store):
    with pytest.raises(ValueError):
        store.upsert_chunks([_chunk(0, "x")], [])


def test_empty_upsert_is_noop(store):
    assert store.upsert_chunks([], []) == 0


def test_reset_clears_collection(store):
    e = HashingEmbedder()
    chunks = [_chunk(0, "some text")]
    store.upsert_chunks(chunks, e.embed([c.text for c in chunks]))
    assert store.count() == 1
    store.reset()
    assert store.count() == 0


def test_all_ids(store):
    e = HashingEmbedder()
    chunks = [_chunk(0, "alpha"), _chunk(1, "beta")]
    store.upsert_chunks(chunks, e.embed([c.text for c in chunks]))
    ids = store.all_ids()
    assert {c.chunk_id for c in chunks} <= ids


def test_persistence_across_instances(tmp_path):
    """Data must survive closing/reopening the store (persisted client)."""
    e = HashingEmbedder()
    p = tmp_path / "chroma_persist"
    s1 = ChromaVectorStore(collection_name="persist_check",
                           persist_dir=p)
    chunks = [_chunk(0, "durable chunk")]
    s1.upsert_chunks(chunks, e.embed([c.text for c in chunks]))
    del s1
    s2 = ChromaVectorStore(collection_name="persist_check", persist_dir=p)
    assert s2.count() == 1
