"""Result containers for the M2 RAG layer.

These dataclasses are the contract between the M1 ingestion JSON, the
chunker, the vector store and the retriever. M3 consumes ``RetrievedChunk``
objects for citation-validated prompting, so provenance fields are
first-class and mandatory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Chunk:
    """A deterministic, provenance-carrying semantic chunk.

    ``chunk_id`` is the ChromaDB primary key (deterministic: re-ingesting
    the same document bytes upserts instead of duplicating).
    """

    chunk_id: str
    document_name: str
    version: str
    sha256: str
    section_no: str                 # "" for title page / front matter
    section_title: str
    page_start: int
    page_end: int
    chunk_seq: int                  # 0-based, ordered by (version, seq)
    chunk_type: str                 # "prose" | "table"
    text: str
    token_count: int

    def metadata(self) -> dict[str, Any]:
        """ChromaDB metadata payload (flat scalar dict, one list field).

        ChromaDB supports only scalar metadata values, so the ordered list
        of pages is stored as a ";"-joined string (``pages_csv``).
        """
        return {
            "document_name": self.document_name,
            "version": self.version,
            "sha256": self.sha256,
            "section_no": self.section_no,
            "section_title": self.section_title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "pages_csv": ";".join(
                str(p) for p in range(self.page_start, self.page_end + 1)),
            "chunk_seq": self.chunk_seq,
            "chunk_type": self.chunk_type,
            "token_count": self.token_count,
        }

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            **self.metadata(),
            "text": self.text,
        }
        return d


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieval hit with its distance and full provenance."""

    chunk_id: str
    text: str
    distance: float                 # cosine distance, lower = more similar
    similarity: float               # 1 - distance, in [0, 1]
    document_name: str
    version: str
    section_no: str
    section_title: str
    page_start: int
    page_end: int
    chunk_type: str
    chunk_seq: int
    token_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": 0,  # filled by the retriever
            "chunk_id": self.chunk_id,
            "score": round(self.similarity, 4),
            "distance": round(self.distance, 6),
            "document_name": self.document_name,
            "version": self.version,
            "section_no": self.section_no,
            "section_title": self.section_title,
            "pages": list(range(self.page_start, self.page_end + 1)),
            "chunk_type": self.chunk_type,
            "chunk_seq": self.chunk_seq,
            "token_count": self.token_count,
            "text": self.text,
        }


@dataclass
class RetrievalResult:
    """Ordered retrieval output (best match first)."""

    query: str
    chunks: list[RetrievedChunk] = field(default_factory=list)
    top_k: int = 5
    filters: dict[str, Any] = field(default_factory=dict)

    def to_dicts(self) -> list[dict[str, Any]]:
        out = [c.to_dict() for c in self.chunks]
        for i, d in enumerate(out):
            d["rank"] = i + 1
        return out
