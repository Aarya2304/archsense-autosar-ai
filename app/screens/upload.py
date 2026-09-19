"""Upload Documents screen (M9, task Parts B/O): user PDF upload workflow.

Explicit, never automatic: selecting files only stages them; the pipeline
(M1 -> M2 -> profile -> profile-gated M4) runs when the user presses
**Process Documents**. Every result — success or per-file failure — is
reported honestly; generic-profile documents get an explicit
"structured analysis unavailable" notice instead of fabricated data.
"""

from __future__ import annotations

import streamlit as st

from app import state
from app.components import fmt_version, profile_badge
from app.services import ServiceError, get_versions_profiled
from app.services.m9_services import process_uploads

# (stage label, stats_json/timings key) — shown per processed document.
_STAGES = [
    ("Ingestion (M1)", "ingest_ms"),
    ("Chunking + indexing (M2)", "index_ms"),
    ("Profile detection", "detect_ms"),
    ("Structured extraction (M4)", "extract_ms"),
]


def render() -> None:
    st.markdown(
        "Upload AUTOSAR or architecture PDFs into the workspace. Processing "
        "starts **only** when you press *Process Documents*; uploads are "
        "stored under the ignored runtime path `data/uploads/`, indexed into "
        "a dedicated vector collection, and never touch the demo corpus.")

    # ------------------------------------------------------------- picker --
    files = st.file_uploader(
        "Choose PDF files", type=["pdf"], accept_multiple_files=True,
        key="up_picker",
        help="PDF only. Large documents may take a minute to index.")

    if files:
        st.markdown("**Selected:**")
        for f in files:
            size_kb = len(f.getvalue()) / 1024.0
            st.markdown(f"- `{f.name}` ({size_kb:,.0f} KB)")

    manual = st.selectbox(
        "Profile detection", ["automatic (evidence-based)", "force "
                              "application_hld", "force autosar_adaptive_"
                              "platform", "force generic"],
        key="up_manual",
        help="Automatic detection inspects title, sections and terminology. "
             "Manual overrides are recorded with the document.")

    manual_profile = None
    if manual.startswith("force "):
        manual_profile = manual.removeprefix("force ").strip()

    if st.button("Process Documents", type="primary", key="up_process",
                 disabled=not files):
        if not files:
            st.warning("Select at least one PDF first.")
        else:
            payload = [(f.getvalue(), f.name) for f in files]
            with st.spinner("Processing uploads (ingestion → indexing → "
                            "profile → extraction)…"):
                results = process_uploads(payload, manual_profile=manual_profile)
            st.session_state.as_last_uploads = results

    _render_results(st.session_state.get("as_last_uploads"))

    st.divider()
    _render_workspace()


def _render_results(results: list[dict] | None) -> None:
    if not results:
        return
    st.subheader("Processing results")
    for res in results:
        name = res.get("original_name", "?")
        if not res.get("ok", False):
            st.error(f"**{name}** failed: {res.get('error', 'unknown error')}")
            continue
        det = res.get("detection", {}) or {}
        evidence = det.get("evidence", {}) or {}
        with st.expander(
                f"✅ {name} — {profile_badge(res.get('profile', 'generic'))}"
                f" · {fmt_version(res.get('version_label', '?'))}"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Pages", res.get("page_count", 0))
            c2.metric("Chunks", res.get("chunk_count", 0))
            c3.metric("Entities", res.get("entity_count", 0))
            c4.metric("Facts", res.get("fact_count", 0))

            st.markdown("**Pipeline stages**")
            timings = res.get("timings_ms", {}) or {}
            stage_rows = []
            for label, key in _STAGES:
                ms = timings.get(key)
                stage_rows.append({
                    "stage": label,
                    "status": "✓ done" if ms is not None
                              else ("— not applicable" if key == "extract_ms"
                                    and not res.get("structured") else "—"),
                    "time": f"{ms:,.0f} ms" if ms is not None else "",
                })
            st.dataframe(stage_rows, use_container_width=True, height=150)

            st.markdown(f"**Profile:** {profile_badge(res.get('profile'))} "
                        f"({res.get('profile', '')}) — {det.get('reason', '')}")
            if evidence:
                st.caption("Detection evidence: " + "; ".join(
                    f"{k} ×{v}" for k, v in sorted(evidence.items())))
            if res.get("duplicate"):
                st.info("Duplicate content: an identical file (same SHA-256) "
                        "was already registered; nothing was re-processed.")
            if not res.get("structured"):
                st.warning(
                    "**Generic profile:** structured architecture analysis "
                    "(graph, findings, comparison) is not available for this "
                    "document. Copilot retrieval works normally over it.")
            issues = res.get("issues", []) or []
            if issues:
                with st.expander("Pipeline notes"):
                    for i in issues:
                        st.caption(f"- `{i.get('kind', '?')}`: "
                                   f"{i.get('detail', '')}")
            st.caption(f"SHA-256 `{res.get('sha256', '')[:16]}…` · stored as "
                       f"`{res.get('safe_name', '')}`")


def _render_workspace() -> None:
    """Currently registered uploads with profile + availability flags."""
    try:
        versions = [v for v in get_versions_profiled() if v["is_upload"]]
    except ServiceError:
        versions = []
    st.subheader("Uploaded documents in this workspace")
    if not versions:
        st.caption("No uploads yet. The synthetic demo corpus remains "
                   "available on every screen.")
        return
    rows = []
    for v in versions:
        rows.append({
            "document": v["document_name"],
            "version": fmt_version(v["version"]),
            "profile": profile_badge(v["profile"]),
            "pages": v["page_count"],
            "chunks": v["chunk_count"],
            "structured analysis": ("available"
                                    if v["structured"] and v["has_registry"]
                                    else "unavailable (M1–M3 only)"),
        })
    st.dataframe(rows, use_container_width=True, height=200)
