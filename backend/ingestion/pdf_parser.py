"""Low-level PDF parsing (M1): metadata, per-page text, OCR fallback hook.

Page text is kept as a list of lines (physical reading order) so downstream
cleaning and section detection can reason about headings vs body lines.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import pymupdf  # PyMuPDF >= 1.26 canonical import
except ImportError:  # pragma: no cover
    import fitz as pymupdf  # type: ignore[no-redef]


SPARSE_THRESHOLD = 40  # characters; below this a page is a scan candidate


@dataclass
class RawPage:
    """Raw, uncleaned page content."""

    page_no: int
    lines: list[str]
    raw_text: str
    char_count: int
    image_count: int


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def open_document(path: Path):
    doc = pymupdf.open(str(path))
    if doc.needs_pass:  # encrypted
        raise ValueError(f"Encrypted PDF not supported: {path.name}")
    return doc


def document_metadata(doc) -> dict[str, Any]:
    meta = doc.metadata or {}
    return {
        "title": (meta.get("title") or "").strip(),
        "author": (meta.get("author") or "").strip(),
        "subject": (meta.get("subject") or "").strip(),
        "creator": (meta.get("creator") or "").strip(),
        "producer": (meta.get("producer") or "").strip(),
        "creation_date": (meta.get("creationDate") or "").strip(),
        "page_count": doc.page_count,
    }


def extract_page_lines(doc, page_no: int) -> RawPage:
    """Extract one page (1-based) as physical-order lines."""
    page = doc[page_no - 1]
    raw = page.get_text("text")
    lines = [ln.rstrip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    return RawPage(
        page_no=page_no,
        lines=lines,
        raw_text=raw,
        char_count=len(raw),
        image_count=len(page.get_images(full=True)),
    )


def is_scan_candidate(raw_page: RawPage) -> bool:
    """True when a page has (almost) no text layer."""
    return raw_page.char_count < SPARSE_THRESHOLD


def ocr_page_lines(path: Path, page_no: int) -> list[str]:
    """OCR fallback hook.

    Replaces heavy OCR imports (pytesseract + Tesseract binary) with a
    lightweight PDF-native heuristic for M1: renders the page region as
    text is unavailable, so we surface an explicit, actionable signal
    instead of pretending to OCR. Real OCR (Tesseract) is a documented
    future enhancement; the synthetic corpus is digital so no milestone
    depends on it.
    """
    doc = open_document(path)
    try:
        raw = extract_page_lines(doc, page_no)
        if not is_scan_candidate(raw):
            return raw.lines
        # Try PyMuPDF's built-in textpage OCR flag if Tesseract is present.
        try:
            page = doc[page_no - 1]
            tp = page.get_textpage(flags=pymupdf.TEXT_PRESERVE_LIGATURES
                                  | pymupdf.TEXT_USE_TEXT_WITH_OCR)
            lines = [ln.rstrip() for ln in tp.extractText().splitlines()]
            if sum(len(l) for l in lines) >= SPARSE_THRESHOLD:
                return [l for l in lines if l.strip()]
        except Exception:  # noqa: BLE001 - OCR optional by design
            pass
        raise RuntimeError(
            f"Page {page_no} of {path.name} has no text layer and OCR is "
            "unavailable. Install Tesseract (future scope) or use a "
            "digital source document.")
    finally:
        doc.close()
