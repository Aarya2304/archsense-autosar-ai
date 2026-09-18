"""Document Workspace screen (M8.5/M8.6): page preview + document map.

LEFT: version/document facts, page + section navigation.
CENTER: rendered PDF page (PyMuPDF SVG, trusted generated markup only).
RIGHT: selected section info and the page's chunk text (M1 provenance).

The ingestion/provenance model stores page/section/chunk provenance but no
per-element bounding boxes, so provenance is shown at page level (M8.6:
coordinates are never fabricated).
"""

from __future__ import annotations

import streamlit as st

from app import state
from app.components import render_page_svg
from app.services import (ServiceError, get_document_summary, get_page_text,
                          get_pdf_path, get_versions)


def render() -> None:
    try:
        versions = get_versions()
    except ServiceError as exc:
        st.error(f"Database unavailable: {exc}")
        return
    if not versions:
        st.warning("No ingested documents. Run the M1 ingestion pipeline "
                   "first.")
        return

    labels = [f"{v['document_name']} — v{v['version']}" for v in versions]
    current = st.session_state.as_version
    idx = next((i for i, v in enumerate(versions)
                if v["version"] == current), 0)
    chosen = st.selectbox("Document / version", labels, index=idx)
    sel = versions[labels.index(chosen)]
    state.set_version(sel["version"])

    summary = get_document_summary(sel["version"])
    if summary is None:
        st.error(f"No processed record for {sel['document_name']} "
                 f"v{sel['version']}.")
        return

    left, center, right = st.columns([2.2, 5, 2.8], gap="medium")

    # ------------------------------------------------------------- left --
    with left:
        st.markdown(f"##### {summary['title']}")
        st.caption(f"Document type: {summary['doc_type']} · "
                   f"Version: {summary['version']}")
        st.metric("Pages", summary["page_count"])
        st.metric("Chunks", summary["chunk_count"])

        pages = summary["pages"] or list(
            range(1, summary["page_count"] + 1))
        page_no = st.selectbox(
            "Page", pages,
            index=pages.index(st.session_state.as_page)
            if st.session_state.as_page in pages else 0)
        st.session_state.as_page = page_no

        sections = summary["sections"]
        if sections:
            st.markdown("**Sections**")
            page_to_sections = {}
            for s in sections:
                page_to_sections.setdefault(s["page"], []).append(s["section_no"])
            for pg in sorted(page_to_sections):
                nos = ", ".join(page_to_sections[pg])
                if st.button(f"p.{pg} — § {nos}",
                             key=f"ws_sec_{pg}",
                             use_container_width=True):
                    st.session_state.as_page = pg
        else:
            st.caption("Section map not available for this document.")

    # ------------------------------------------------------------ center --
    with center:
        st.markdown("##### Page preview")
        pdf_path = get_pdf_path(summary["document_name"])
        if pdf_path is None:
            st.info(
                "Source PDF not found on disk — showing the page's indexed "
                "text instead (right panel). Place the PDF under "
                "`data/sample_docs/` to enable the rendered preview.")
        else:
            zoom = st.slider("Zoom", 1.0, 3.0, 1.5, 0.25, key="ws_zoom")
            try:
                render_page_svg(str(pdf_path), page_no, zoom)
            except FileNotFoundError:
                st.error(f"PDF disappeared: {pdf_path}")
            except ValueError as exc:
                st.error(f"Invalid page: {exc}")
        st.caption(
            "Provenance level: page (the extraction pipeline stores "
            "page/section/chunk references, not coordinate boxes, so no "
            "highlight rectangles are drawn).")

    # ------------------------------------------------------------- right --
    with right:
        st.markdown("##### Selected page")
        section_nos = [s["section_no"] for s in summary["sections"]
                       if s["page"] == page_no]
        if section_nos:
            st.info(f"Sections starting here: § {', '.join(section_nos)}")
        text = get_page_text(summary["document_name"], summary["version"],
                             page_no)
        if text.strip():
            st.markdown("**Indexed chunk text (M1)**")
            st.text_area("page text", value=text, height=420,
                         label_visibility="collapsed",
                         disabled=True)
        else:
            st.caption("No indexed chunks start on this page "
                       "(page covered by a chunk spanning other pages).")

    # Section buttons (left column) set as_page during rendering, after the
    # page selectbox read it; re-route so one click takes effect immediately.
    if st.session_state.as_page != page_no:
        st.rerun()
