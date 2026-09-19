"""Dashboard screen (M8.4): landing page with workspace overview + metrics.

All numbers come from the backend subsystems (M1 versions, M4/M5 registry,
M6 findings, M7 comparison summary) — nothing is invented in the UI.
"""

from __future__ import annotations

import streamlit as st

from app import state
from app.components import SCREENS, profile_badge
from app.services import (ServiceError, get_architecture_stats,
                          get_findings_from_db, get_versions_profiled)


def _action_card(label: str, description: str, screen: str, key: str) -> None:
    if st.button(f"**{label}**", use_container_width=True, key=key):
        state.set_screen(screen)
    st.caption(description)


def render() -> None:
    st.markdown(
        "Engineering analysis workspace for AUTOSAR-style HLD documents: "
        "grounded Q&A over ingested PDFs, structured architecture extraction, "
        "deterministic findings, and revision comparison — every result "
        "traceable to document, section, page and chunk."
    )

    try:
        versions = get_versions_profiled()
    except ServiceError as exc:
        st.error(f"Database unavailable: {exc}")
        return

    if not versions:
        st.warning(
            "No ingested documents found. Run the M1 ingestion pipeline "
            "(scripts/build_dataset.py) first.")
        return

    # workspace selector shared by every screen (session state, D-043)
    labels = [f"{v['document_name']} — v{v['version']}" for v in versions]
    current = st.session_state.as_version
    idx = next((i for i, v in enumerate(versions)
                if v["version"] == current), 0)
    chosen = st.selectbox(
        "Workspace (document / version)", labels, index=idx)
    sel = versions[labels.index(chosen)]
    state.set_version(sel["version"])

    st.caption(f"Profile: {profile_badge(sel['profile'])} · "
               + ("structured analysis available" if sel["structured"]
                  and sel["has_registry"] else "M1–M3 retrieval only"))

    st.divider()

    # metrics row ---------------------------------------------------------
    mcols = st.columns(4)
    stats = None
    if sel["has_registry"]:
        try:
            stats = get_architecture_stats(sel["version"])
        except ServiceError:
            stats = None
    findings = (get_findings_from_db(sel["version"])
                if sel["has_registry"] else [])

    mcols[0].metric("Pages (ingested)", sel["page_count"])
    mcols[1].metric("Chunks (M2 index)",
                    sel["chunk_count"] if sel["has_chunks"] else "—")
    mcols[2].metric(
        "Entities / facts (M4)",
        f"{stats['node_count']} / {stats['edge_count']}" if stats else "—")
    mcols[3].metric("Findings persisted (M6)", len(findings))

    flags = []
    if not sel["has_chunks"]:
        flags.append("no chunk index (M2) — Copilot will be unavailable")
    if not sel["has_registry"]:
        flags.append("no structured extraction (M4) — Explorer / Findings / "
                     "Compare unavailable for this version")
    if sel["profile"] == "generic":
        flags.append("generic profile — structured analysis is intentionally "
                     "unavailable; Copilot retrieval is fully supported")
    for f in flags:
        st.caption(f"⚠ {f}")

    st.subheader("Open a screen")
    rows = [SCREENS[i:i + 3] for i in range(0, len(SCREENS), 3)]
    for r_i, row in enumerate(rows):
        cols = st.columns(3)
        for c_i, (screen_id, label) in enumerate(row):
            with cols[c_i]:
                descriptions = {
                    state.UPLOAD: "Upload and process new PDF documents "
                                  "(explicit, profile-aware pipeline).",
                    state.WORKSPACE: "Browse pages, sections and chunk text "
                                     "of the selected document.",
                    state.EXPLORER: "Interactive architecture graph with "
                                    "entity details and provenance.",
                    state.COPILOT: "Ask grounded questions; answers carry "
                                   "validated citations.",
                    state.FINDINGS: "Run and review deterministic "
                                    "architecture findings.",
                    state.COMPARE: "Diff two revisions and inspect potential "
                                   "impact.",
                    state.EXPORT: "Generate the deterministic analysis "
                                  "report (JSON/CSV).",
                }
                _action_card(label, descriptions.get(screen_id, ""),
                             screen_id, f"dash_{screen_id}")

    st.divider()
    st.caption(
        "Data sources: SQLite registry (M1/M4/M6/M7), ChromaDB index (M2/M3), "
        "NetworkX graph (M5). Structured architecture analysis is profile-"
        "aware: the synthetic ABC application HLD and the AUTOSAR Adaptive "
        "Platform profile both support M4–M7, generic documents are served "
        "by Copilot retrieval (M1–M3) only — nothing is fabricated.")
