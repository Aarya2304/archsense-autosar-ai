"""Vector store abstraction + ChromaDB implementation (M2.3).

The rest of the application depends only on the ``VectorStore`` protocol and
the plain ``StoredChunk`` dataclass -- never on ChromaDB APIs directly
(D-013: swappable, testable). ChromaDB persists under ``data/vectors/chroma``
(git-ignored).

Duplicate prevention: chunk IDs are deterministic (content-addressed), and
``upsert_chunks`` uses Chroma's ``upsert`` semantics -- re-indexing the same
document never creates duplicates (M2 completion criterion).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from backend.config import VECTORS_DIR
from backend.rag.models import RetrievedChunk

CHROMA_SUBDIR = "chroma"


@dataclass(frozen=True)
class StoredChunk:
    """A chunk as stored in / returned from the vector store."""

    chunk_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VectorStore(Protocol):
    """Minimal contract the retriever (M2.4) and index builder (M2.5) use."""

    def upsert_chunks(self, chunks: Sequence[StoredChunk],
                      embeddings: Sequence[Sequence[float]]) -> int:
        """Insert-or-update chunks; returns the collection size."""
        ...

    def count(self) -> int:
        """Number of vectors currently in the collection."""
        ...

    def query(self, embedding: Sequence[float], top_k: int = 5,
              where: dict[str, Any] | None = None) -> list[RetrievedChunk]:
        """Nearest-neighbour query; ``where`` is a metadata filter dict."""
        ...

    def reset(self) -> None:
        """Delete all vectors (development rebuild path)."""
        ...

    def get_all_chunks(self) -> list[RetrievedChunk]:
        """Every stored chunk with provenance (M3 lexical mirror index).

        Order is unspecified; callers that need determinism sort before use.
        """
        ...


def _decode_pages_csv(meta: dict[str, Any]) -> tuple[int, int]:
    """Recover (page_start, page_end) from the flat metadata payload."""
    start = int(meta.get("page_start", 0) or 0)
    end = int(meta.get("page_end", start) or start)
    return start, end


class ChromaVectorStore:
    """ChromaDB-backed vector store (cosine space, persisted locally)."""

    def __init__(self, collection_name: str = "archsense_chunks",
                 persist_dir: Path | None = None) -> None:
        import chromadb  # lazy: keep import cost off unit tests

        self._persist_dir = Path(persist_dir) if persist_dir else \
            VECTORS_DIR / CHROMA_SUBDIR
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._persist_dir))
        self._collection_name = collection_name
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    # ---------------------------------------------------------------- API --
    @property
    def name(self) -> str:
        return f"chroma:{self._collection_name}"

    def upsert_chunks(self, chunks: Sequence[StoredChunk],
                      embeddings: Sequence[Sequence[float]]) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks/embeddings length mismatch: "
                f"{len(chunks)} != {len(embeddings)}")
        if not chunks:
            return self.count()
        self._collection.upsert(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[dict(c.metadata) for c in chunks],
            embeddings=[list(map(float, e)) for e in embeddings],
        )
        return self.count()

    def count(self) -> int:
        return int(self._collection.count())

    def query(self, embedding: Sequence[float], top_k: int = 5,
              where: dict[str, Any] | None = None) -> list[RetrievedChunk]:
        res = self._collection.query(
            query_embeddings=[list(map(float, embedding))],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[RetrievedChunk] = []
        ids = res.get("ids") or [[]]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, chunk_id in enumerate(ids[0]):
            meta = dict(metas[i]) if i < len(metas) else {}
            page_start, page_end = _decode_pages_csv(meta)
            distance = float(dists[i]) if i < len(dists) else 1.0
            hits.append(RetrievedChunk(
                chunk_id=chunk_id,
                text=docs[i] if i < len(docs) else "",
                distance=distance,
                similarity=1.0 - distance,
                document_name=str(meta.get("document_name", "")),
                version=str(meta.get("version", "")),
                section_no=str(meta.get("section_no", "")),
                section_title=str(meta.get("section_title", "")),
                page_start=page_start,
                page_end=page_end,
                chunk_type=str(meta.get("chunk_type", "")),
                chunk_seq=int(meta.get("chunk_seq", 0) or 0),
                token_count=int(meta.get("token_count", 0) or 0),
            ))
        return hits

    def reset(self) -> None:
        try:
            self._client.delete_collection(self._collection_name)
        except Exception:  # collection may not exist yet
            pass
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def all_ids(self) -> set[str]:
        """Every chunk ID currently stored (dup/consistency checks, tests)."""
        n = self.count()
        if n == 0:
            return set()
        got = self._collection.get(include=[])
        return set(got.get("ids") or [])

    def get_all_chunks(self) -> list[RetrievedChunk]:
        """Every stored chunk as a ``RetrievedChunk`` (M3 lexical index)."""
        n = self.count()
        if n == 0:
            return []
        got = self._collection.get(
            include=["documents", "metadatas"])
        ids = got.get("ids") or []
        docs = got.get("documents") or []
        metas = got.get("metadatas") or []
        hits: list[RetrievedChunk] = []
        for i, chunk_id in enumerate(ids):
            meta = dict(metas[i]) if i < len(metas) else {}
            page_start, page_end = _decode_pages_csv(meta)
            hits.append(RetrievedChunk(
                chunk_id=chunk_id,
                text=docs[i] if i < len(docs) else "",
                distance=0.0,
                similarity=0.0,
                document_name=str(meta.get("document_name", "")),
                version=str(meta.get("version", "")),
                section_no=str(meta.get("section_no", "")),
                section_title=str(meta.get("section_title", "")),
                page_start=page_start,
                page_end=page_end,
                chunk_type=str(meta.get("chunk_type", "")),
                chunk_seq=int(meta.get("chunk_seq", 0) or 0),
                token_count=int(meta.get("token_count", 0) or 0),
            ))
        return hits


def get_vector_store(collection_name: str = "archsense_chunks",
                     persist_dir: Path | None = None) -> ChromaVectorStore:
    """Default store factory (single point where ChromaDB is constructed)."""
    return ChromaVectorStore(collection_name=collection_name,
                             persist_dir=persist_dir)
