"""Ingestion -> vector index pipeline (M2.5).

    PDF --(M1 ingestion)--> *__processed.json --(chunk)--> chunks
         --(embed)--> vectors --(upsert)--> ChromaDB

``index_processed_document`` is the single entry point: it loads a saved M1
ingestion record (ingesting the PDF first when absent), chunks it
deterministically, embeds with the configured provider, and upserts into the
vector store. Re-running is idempotent (deterministic chunk IDs + upsert
semantics), and ``rebuild=True`` wipes the collection first for clean
development cycles. No LLM is involved anywhere in this pipeline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from backend.config import PROCESSED_DIR
from backend.ingestion.pipeline import ingest_pdf, process_document
from backend.rag.chunker import chunk_ingestion_result
from backend.rag.embedder import EmbeddingProvider
from backend.rag.models import Chunk
from backend.rag.vector_store import VectorStore


@dataclass
class IndexSummary:
    """What one indexing run did (returned by ``index_processed_document``)."""

    document_name: str
    version: str
    n_chunks: int
    n_prose: int
    n_tables: int
    collection_size: int
    embed_seconds: float
    upsert_seconds: float
    reused_existing_ingestion: bool

    def to_dict(self) -> dict:
        return {
            "document_name": self.document_name,
            "version": self.version,
            "n_chunks": self.n_chunks,
            "n_prose_chunks": self.n_prose,
            "n_table_chunks": self.n_tables,
            "collection_size": self.collection_size,
            "embed_seconds": round(self.embed_seconds, 3),
            "upsert_seconds": round(self.upsert_seconds, 3),
            "reused_existing_ingestion": self.reused_existing_ingestion,
        }


def _processed_path_for(pdf_path: Path) -> Path:
    """Processed-JSON path convention shared with scripts/process_sample_docs.py."""
    return PROCESSED_DIR / f"{pdf_path.stem}__processed.json"


def index_processed_document(
    processed_json: Path,
    embedder: EmbeddingProvider,
    store: VectorStore,
    rebuild: bool = False,
    version: str | None = None,
    target_tokens: int = 500,
    overlap_tokens: int = 75,
) -> tuple[list[Chunk], IndexSummary]:
    """Chunk + embed + upsert one saved ingestion record.

    Returns ``(chunks, summary)``; the chunks are also returned so callers
    (tests, reports) can inspect exactly what was indexed without re-chunking.
    """
    import json

    processed_json = Path(processed_json)
    if not processed_json.exists():
        raise FileNotFoundError(
            f"No processed ingestion record at {processed_json}. Run "
            f"backend.ingestion.pipeline.process_document first.")
    ingested = json.loads(processed_json.read_text(encoding="utf-8"))

    if rebuild:
        store.reset()

    chunks = chunk_ingestion_result(ingested, version=version,
                                    target_tokens=target_tokens,
                                    overlap_tokens=overlap_tokens)
    if not chunks:
        raise ValueError(f"Chunker produced no chunks for {processed_json}")

    texts = [c.text for c in chunks]
    t0 = time.perf_counter()
    vectors = embedder.embed(texts)
    embed_seconds = time.perf_counter() - t0

    from backend.rag.vector_store import StoredChunk
    stored = [StoredChunk(chunk_id=c.chunk_id, text=c.text,
                          metadata=c.metadata()) for c in chunks]
    t0 = time.perf_counter()
    collection_size = store.upsert_chunks(stored, vectors)
    upsert_seconds = time.perf_counter() - t0

    versions = {c.version for c in chunks}
    summary = IndexSummary(
        document_name=ingested["document_name"],
        version=versions.pop() if len(versions) == 1 else str(versions),
        n_chunks=len(chunks),
        n_prose=sum(1 for c in chunks if c.chunk_type == "prose"),
        n_tables=sum(1 for c in chunks if c.chunk_type == "table"),
        collection_size=collection_size,
        embed_seconds=embed_seconds,
        upsert_seconds=upsert_seconds,
        reused_existing_ingestion=True,
    )
    return chunks, summary


def index_pdf(
    pdf_path: Path,
    embedder: EmbeddingProvider,
    store: VectorStore,
    rebuild: bool = False,
    **kwargs,
) -> tuple[list[Chunk], IndexSummary]:
    """Index a source PDF, running M1 ingestion first if needed.

    If a processed record already exists (by filename convention) it is
    reused; otherwise ``process_document`` runs and saves it.
    """
    pdf_path = Path(pdf_path)
    processed = _processed_path_for(pdf_path)
    reused = processed.exists()
    if not reused:
        process_document(pdf_path, out_dir=PROCESSED_DIR)
    chunks, summary = index_processed_document(
        processed, embedder, store, rebuild=rebuild, **kwargs)
    summary.reused_existing_ingestion = reused
    return chunks, summary


__all__ = ["IndexSummary", "index_pdf", "index_processed_document",
           "ingest_pdf"]
