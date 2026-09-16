"""Table extraction (M1), built on pdfplumber with PyMuPDF fallback.

Tables are kept as header/row matrices anchored to a page. The pipeline also
derives a text-linearized representation (used by M2 chunking) and exposes
rows for prose-vs-table consistency checks (M6).
"""

from __future__ import annotations

from backend.ingestion.models import TableRecord


def extract_tables(doc, page_no: int) -> list[TableRecord]:
    """Extract tables from one page (1-based) via pdfplumber.

    ``doc`` is an open PyMuPDF document; we take its file path and open a
    pdfplumber handle per page batch to keep memory bounded.
    """
    import pdfplumber  # local import: optional at import time

    path = doc.name
    records: list[TableRecord] = []
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_no - 1]
        found = page.extract_tables() or []
        for idx, table in enumerate(found):
            rows = _normalize_rows(table)
            if not rows:
                continue
            headers = rows[0]
            body = rows[1:]
            records.append(TableRecord(
                page_no=page_no,
                table_index=idx,
                headers=headers,
                rows=body,
                n_rows=len(body),
                n_cols=len(headers),
            ))
    return records


def _normalize_rows(table: list[list[str | None]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if any(cells):
            rows.append(cells)
    return rows


def linearize_table(headers: list[str], rows: list[list[str]]) -> str:
    """Text form of a table for embedding (M2): one line per row."""
    header_line = " | ".join(h for h in headers if h)
    lines = [f"Table columns: {header_line}"]
    for row in rows:
        lines.append(" | ".join(str(c) for c in row))
    return "\n".join(lines)


def table_row_strings(record: TableRecord) -> list[str]:
    """Rows as searchable strings (keys + values), for consistency checks."""
    out: list[str] = []
    for row in record.rows:
        out.append(" | ".join(
            f"{h}: {v}" for h, v in zip(record.headers, row) if h or v))
    return out
