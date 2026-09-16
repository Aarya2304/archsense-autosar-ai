"""Page-aware ingestion pipeline (M1).

Orchestrates: parse -> clean -> OCR-fallback decision -> table extraction ->
heading/section detection -> page records. Output is an ``IngestionResult``
serialized to JSON for M2 chunking and the UI.

Every downstream feature (RAG citations, extraction, analysis) relies on
this stage being page-accurate, so it is covered by dedicated tests.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from backend.config import PROCESSED_DIR
from backend.ingestion import pdf_parser, sections, table_extractor
from backend.ingestion.cleaning import assemble_page_text, clean_lines
from backend.ingestion.models import IngestionResult, PageRecord
from backend.ingestion.metadata_extractor import (
    collect_document_metadata, guess_version)


def ingest_pdf(path: Path, version_label: str | None = None,
               collect_tables: bool = True) -> IngestionResult:
    """
    Ingest one PDF end-to-end.

    Returns an IngestionResult with page records (cleaned lines + sections),
    table records, document metadata, and timing stats.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    t0 = time.perf_counter()
    doc = pdf_parser.open_document(path)
    try:
        sha = pdf_parser.compute_sha256(path)
        meta = collect_document_metadata(path, doc, sha)

        pages: list[PageRecord] = []
        tables = []
        ocr_used_pages = 0

        # ---------------- per-page text + tables ----------------
        for page_no in range(1, doc.page_count + 1):
            raw = pdf_parser.extract_page_lines(doc, page_no)
            lines = raw.lines

            if pdf_parser.is_scan_candidate(raw):
                try:
                    lines = pdf_parser.ocr_page_lines(path, page_no)
                    ocr_used_pages += 1
                    ocr_used = True
                except RuntimeError:
                    lines = []
                    ocr_used = False
            else:
                ocr_used = False

            cleaned, dropped = clean_lines(lines)
            page_text = assemble_page_text(cleaned)
            words = len(page_text.split())

            pages.append(PageRecord(
                page_no=page_no,
                text=page_text,
                char_count=len(page_text),
                has_table=False,
                sections=[],           # filled after heading detection
                ocr_used=ocr_used,
                word_count=words,
                lines=cleaned,
            ))

        # ---------------- tables ----------------
        if collect_tables:
            for page_no in range(1, doc.page_count + 1):
                page_tables = table_extractor.extract_tables(doc, page_no)
                tables.extend(page_tables)
                if page_tables:
                    pages[page_no - 1].has_table = True
                    pages[page_no - 1].table_count = len(page_tables)

        # ---------------- headings / sections ----------------
        pages_lines = {p.page_no: p.lines for p in pages}
        headings = sections.detect_headings(pages_lines)
        section_map = sections.build_section_map(headings)
        for p in pages:
            # sections starting on this page (needed for M2 chunking)
            p.sections = sorted(h.section_no for h in headings
                                if h.page_no == p.page_no)

        elapsed = time.perf_counter() - t0
        result = IngestionResult(
            document_name=path.name,
            file_path=str(path),
            sha256=sha,
            page_count=doc.page_count,
            metadata=meta,
            pages=pages,
            tables=tables,
            sections=section_map,
            stats={
                "elapsed_seconds": round(elapsed, 3),
                "ocr_used_pages": ocr_used_pages,
                "total_lines_kept": sum(len(p.lines) for p in pages),
                "total_chars": sum(p.char_count for p in pages),
                "total_words": sum(p.word_count for p in pages),
                "table_count": len(tables),
                "section_count": len(section_map),
            },
        )
        return result
    finally:
        doc.close()


def save_result(result: IngestionResult, out_path: Path) -> Path:
    """Persist IngestionResult as JSON (M2 chunker + UI consume this)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    payload["stats"]["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def process_document(path: Path, version_label: str | None = None,
                     out_dir: Path | None = None) -> Path:
    """Ingest + save processed JSON. Returns the output JSON path."""
    result = ingest_pdf(path, version_label=version_label)
    out_dir = out_dir or PROCESSED_DIR
    stem = Path(result.document_name).stem
    out = out_dir / f"{stem}__processed.json"
    save_result(result, out)
    return out


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--version", default=None)
    args = ap.parse_args()
    out = process_document(Path(args.pdf), args.version)
    print(f"processed -> {out}")
