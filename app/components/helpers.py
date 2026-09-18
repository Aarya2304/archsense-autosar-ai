"""Application-level constants and shared formatting helpers (M8).

The PDF page preview uses PyMuPDF's built-in SVG output (``page.get_svg_image``
— pure Python string, no external rasterizer dependency), rendered via
``st.iframe`` (srcdoc). Only backend-generated SVG is ever placed into the
iframe (D-044) — no user-provided HTML is rendered anywhere in the app.
"""

from __future__ import annotations

import html

import streamlit as st

from app import state

SCREENS = [
    (state.DASHBOARD, "Dashboard"),
    (state.WORKSPACE, "Document Workspace"),
    (state.EXPLORER, "Architecture Explorer"),
    (state.COPILOT, "Copilot"),
    (state.FINDINGS, "Findings"),
    (state.COMPARE, "Revision Compare"),
    (state.EXPORT, "Export & Report"),
]


def esc(text) -> str:
    """HTML-escape untrusted text before embedding into generated HTML."""
    return html.escape(str(text))


def fmt_provenance(document: str | None,
                   version: str | None = None,
                   section_no: str | None = None,
                   section_title: str | None = None,
                   page_start: int | None = None,
                   page_end: int | None = None,
                   chunk_id: str | None = None) -> str:
    """One-line provenance string used across screens, e.g.

    ``ABC_HLD_v1.1.0.pdf · v1.1.0 · Section 4.1 DoorStatusIF (IF-01) · p.9 · chunk bdbb…``
    """
    parts: list[str] = []
    if document:
        parts.append(str(document))
    if version:
        v = str(version)
        parts.append(v if v.startswith("v") else f"v{v}")
    if section_no:
        sec = f"Section {section_no}"
        if section_title:
            sec += f" {section_title}"
        parts.append(sec)
    if page_start is not None:
        rng = f"p.{page_start}"
        if page_end is not None and page_end != page_start:
            rng = f"pp.{page_start}-{page_end}"
        parts.append(rng)
    if chunk_id:
        parts.append(f"chunk {chunk_id[:8]}…")
    return " · ".join(parts)


def provenance_from_dict(p: dict) -> str:
    """Format a provenance/evidence dict (M4/M5/M6/M7 keys) into one line."""
    return fmt_provenance(
        document=p.get("document") or p.get("document_name"),
        version=p.get("version"),
        section_no=str(p["section_no"]) if p.get("section_no") is not None else None,
        section_title=p.get("section_title"),
        page_start=p.get("page_start"),
        page_end=p.get("page_end"),
        chunk_id=p.get("source_chunk_id") or p.get("chunk_id"),
    )


def render_page_svg(pdf_path: str, page_no: int, zoom: float = 1.5) -> None:
    """Render one PDF page as inline SVG (PyMuPDF built-in, offline).

    Only the backend-produced SVG string is embedded (D-044). Raises
    ``FileNotFoundError`` / ``ValueError`` for missing file/page — callers turn
    those into user-facing errors.
    """
    import fitz  # PyMuPDF

    with fitz.open(pdf_path) as doc:
        if page_no < 1 or page_no > doc.page_count:
            raise ValueError(f"page {page_no} out of range 1..{doc.page_count}")
        page = doc[page_no - 1]
        svg = page.get_svg_image(matrix=fitz.Matrix(zoom, zoom))

    st.iframe(
        f"""
        <div style="font-family:Source Sans Pro,sans-serif;color:#313338;
                    max-height:640px;overflow:auto;border:1px solid #ddd;
                    border-radius:6px;background:#fff;">
          {svg}
        </div>
        """,
        height=660,
    )
