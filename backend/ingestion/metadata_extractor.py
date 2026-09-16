"""Metadata extraction (M1).

Pulls PDF metadata plus document-level facts used by versioning and the UI:
title/version guesses, file identity (sha256, size), and counts. Version
detection is intentionally simple: an explicit version label is supplied by
the caller when known; otherwise we attempt a light heuristic from the
title-page text (e.g. "1.0.0") and fall back to "unversioned".
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.ingestion import pdf_parser
from backend.ingestion.models import IngestionResult

_VERSION_RE = re.compile(r"\bV(?:ersion)?\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)\b",
                         re.IGNORECASE)
_DOTTED_VERSION_RE = re.compile(r"\b([0-9]+\.[0-9]+\.[0-9]+)\b")


def guess_version(text: str) -> str | None:
    m = _VERSION_RE.search(text) or _DOTTED_VERSION_RE.search(text)
    return m.group(1) if m else None


def collect_document_metadata(path: Path, doc, sha256: str,
                              extra: dict | None = None) -> dict:
    meta = pdf_parser.document_metadata(doc)
    size_bytes = path.stat().st_size
    out = {
        "file_name": path.name,
        "file_size_bytes": size_bytes,
        "sha256": sha256,
        **meta,
        **(extra or {}),
    }
    return out


def result_summary(result: IngestionResult) -> dict:
    return {
        "pages": result.page_count,
        "tables": len(result.tables),
        "chars": sum(p.char_count for p in result.pages),
        "words": sum(p.word_count for p in result.pages),
        "ocr_pages": sum(1 for p in result.pages if p.ocr_used),
        "sections": len(result.sections),
    }
