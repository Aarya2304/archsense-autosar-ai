"""Result containers for the ingestion pipeline (M1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PageRecord:
    """Everything we know about one PDF page."""

    page_no: int                      # 1-based
    text: str                         # cleaned page text
    char_count: int
    has_table: bool
    sections: list[str]               # section numbers first seen on page
    table_count: int = 0
    ocr_used: bool = False
    word_count: int = 0
    lines: list[str] = field(default_factory=list)


@dataclass
class TableRecord:
    """An extracted table with its page anchor."""

    page_no: int
    table_index: int                  # 0-based within page
    headers: list[str]
    rows: list[list[str]]
    n_rows: int
    n_cols: int


@dataclass
class IngestionResult:
    """Full result of ingesting one PDF."""

    document_name: str
    file_path: str
    sha256: str
    page_count: int
    metadata: dict[str, Any]
    pages: list[PageRecord]
    tables: list[TableRecord]
    sections: dict[str, int]          # section_no -> first page
    stats: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "document_name": self.document_name,
            "file_path": self.file_path,
            "sha256": self.sha256,
            "page_count": self.page_count,
            "metadata": self.metadata,
            "sections": self.sections,
            "stats": self.stats,
            "pages": [
                {
                    "page_no": p.page_no,
                    "char_count": p.char_count,
                    "word_count": p.word_count,
                    "has_table": p.has_table,
                    "table_count": p.table_count,
                    "ocr_used": p.ocr_used,
                    "sections": p.sections,
                    "lines": p.lines,
                }
                for p in self.pages
            ],
            "tables": [
                {
                    "page_no": t.page_no,
                    "table_index": t.table_index,
                    "headers": t.headers,
                    "rows": t.rows,
                    "n_rows": t.n_rows,
                    "n_cols": t.n_cols,
                }
                for t in self.tables
            ],
        }
