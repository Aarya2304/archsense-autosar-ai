"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def dataset(tmp_path_factory):
    """Build the synthetic dataset once per test session.

    PDFs are rendered into a temp dir copy? No - the canonical dataset is
    regenerated in the standard location (data/) once per session, and
    tests that need isolation copy artifacts they mutate.
    """
    from backend.dataset.ground_truth import build_all

    return build_all(force=True)


# ------------------------------------------------------------------ M2 ----

@pytest.fixture(scope="session")
def ingested_v1(dataset) -> dict:
    """IngestionResult dict for HLD v1 (session-wide, read-only use)."""
    from backend.dataset.ground_truth import V1_PDF_PATH
    from backend.ingestion.pipeline import ingest_pdf

    return ingest_pdf(Path(V1_PDF_PATH)).to_dict()


@pytest.fixture(scope="session")
def processed_jsons(dataset) -> list[Path]:
    """Saved *__processed.json paths for both HLD versions (M2 fixtures)."""
    from backend.config import PROCESSED_DIR
    from backend.dataset.ground_truth import V1_PDF_PATH, V2_PDF_PATH
    from backend.ingestion.pipeline import process_document

    out = []
    for pdf in (Path(V1_PDF_PATH), Path(V2_PDF_PATH)):
        out.append(Path(process_document(pdf, out_dir=PROCESSED_DIR)))
    return out


@pytest.fixture(scope="session")
def rag_store(processed_jsons, tmp_path_factory):
    """ChromaDB store over the full synthetic corpus (hashing embedder).

    Session-scoped so retrieval/evaluation tests share one build; tests that
    mutate the store must create their own (see ``fresh_rag_store``).
    """
    from backend.rag.embedder import HashingEmbedder
    from backend.rag.indexing import index_processed_document
    from backend.rag.vector_store import ChromaVectorStore

    store = ChromaVectorStore(
        collection_name="test_session_chunks",
        persist_dir=tmp_path_factory.mktemp("chroma_session"))
    embedder = HashingEmbedder()
    for pj in processed_jsons:
        index_processed_document(pj, embedder, store, rebuild=(pj is processed_jsons[0]))
    return store


@pytest.fixture(scope="session")
def rag_service(rag_store) -> "RetrievalService":
    """Retrieval service over the session corpus (hashing embedder)."""
    from backend.rag.embedder import HashingEmbedder
    from backend.rag.retriever import RetrievalService

    return RetrievalService(embedder=HashingEmbedder(), store=rag_store)


@pytest.fixture()
def fresh_rag_store(tmp_path):
    """Empty per-test ChromaDB store (mutation tests, dedupe/reset checks)."""
    from backend.rag.vector_store import ChromaVectorStore

    return ChromaVectorStore(collection_name="test_unit_chunks",
                             persist_dir=tmp_path / "chroma")
