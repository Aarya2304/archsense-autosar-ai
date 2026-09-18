"""Findings screen (M8.11/M8.12): M6 deterministic analysis + review state.

A fresh, never-cached FindingEngine run is triggered explicitly by the user
(persisting only via the M6 idempotent, review-preserving persistence).
Persisted findings are displayed read-only with filters; the review-status
workflow is left to M8 follow-up because M6's safe per-finding review update
is not exposed as a UI-safe operation yet — status is displayed, never
silently modified (M8.12).
"""

from __future__ import annotations

import streamlit as st

from app import state
from app.components import fmt_provenance
from app.services import (ServiceError, analyze_findings,
                          filter_findings, findings_breakdown,
                          get_findings_from_db, get_versions)

_SEVERITIES = ["high", "medium", "low", "info"]
_STATUSES = ["open", "accepted", "rejected", "needs_discussion"]


def render() -> None:
    try:
        versions = [v for v in get_versions() if v["has_registry"]]
    except ServiceError as exc:
        st.error(f"Database unavailable: {exc}")
        return
    if not versions:
        st.warning(
            "No structured architecture available. Run the M4 extraction "
            "pipeline first (scripts/extract_entities.py).")
        return

    labels = [f"{v['document_name']} — v{v['version']}" for v in versions]
    current = st.session_state.as_version
    idx = next((i for i, v in enumerate(versions)
                if v["version"] == current), 0)
    chosen = st.selectbox("Version", labels, index=idx)
    sel = versions[labels.index(chosen)]
    state.set_version(sel["version"])

    acols = st.columns([2, 2, 1.4])
    with acols[0]:
        if st.button("Run analysis (M6 detectors)",
                     type="primary", key="fd_run"):
            with st.spinner("Running deterministic detectors…"):
                try:
                    st.session_state.as_last_findings = analyze_findings(
                        sel["version"])
                except ServiceError as exc:
                    st.session_state.as_last_findings = None
                    st.error(str(exc))
    with acols[1]:
        st.caption("Fresh, deterministic run over the M4 registry + M5 "
                   "graph. Persisting goes through the M6 idempotent "
                   "persistence and never changes existing review states.")

    result = st.session_state.as_last_findings
    if result is None:
        persisted = get_findings_from_db(sel["version"])
        if persisted:
            st.info(f"Showing the latest persisted analysis for v{sel['version']} "
                    f"({len(persisted)} findings). Run a fresh analysis for "
                    "current registry state.")
            _render_list(persisted, read_only=True)
        else:
            st.info("No findings yet. Run the analysis to detect "
                    "architecture issues.")
        return

    st.session_state.as_last_findings = result
    if result is not None:
        st.session_state["as_last_report"] = None  # stale for a new run
    _render_run(result)


def _render_run(result: dict) -> None:
    findings = result.get("findings", [])
    if not result.get("validation_ok", True):
        st.error("Validation issues in the last run: "
                 + "; ".join(i.get("rule", "?") + ": "
                             + i.get("message", "")
                             for i in result.get("validation_issues", [])))

    mcols = st.columns(4)
    by_sev = result.get("by_severity", {})
    mcols[0].metric("Findings", len(findings))
    mcols[1].metric("High severity", by_sev.get("high", 0))
    mcols[2].metric("Medium severity", by_sev.get("medium", 0))
    mcols[3].metric("Low / info",
                    by_sev.get("low", 0) + by_sev.get("info", 0))

    pcols = st.columns([1, 1, 1])
    with pcols[0]:
        sev = st.multiselect("Severity", _SEVERITIES, key="fd_sev")
    with pcols[1]:
        ftypes = sorted({f.get("finding_type", "?") for f in findings})
        ftyp = st.multiselect("Finding type", ftypes, key="fd_ftype")
    with pcols[2]:
        stats = st.selectbox("Persist results",
                             ["do not persist", "persist to registry"],
                             key="fd_persist")

    filtered = filter_findings(
        findings,
        severity=sev[0] if len(sev) == 1 else None,
        finding_type=ftyp[0] if len(ftyp) == 1 else None,
    )
    _render_list(filtered)

    if stats == "persist to registry" and st.button(
            "Confirm persistence", key="fd_persist_confirm"):
        try:
            outcome = analyze_findings(result["version"], persist=True)
            st.success(f"Persisted: {outcome.get('persist_stats')}")
        except ServiceError as exc:
            st.error(str(exc))


def _render_list(findings: list[dict], read_only: bool = False) -> None:
    breakdown = findings_breakdown(findings)
    if breakdown["by_type"]:
        st.caption("By type: " + ", ".join(
            f"{k} ×{v}" for k, v in breakdown["by_type"].items()))
    for f in findings:
        sev = f.get("severity", "?")
        header = (f"[{sev.upper()}] {f.get('finding_type')} — "
                  f"{f.get('title', '')}")
        with st.expander(header):
            c1, c2 = st.columns([3, 2])
            with c1:
                st.markdown(f.get("description", ""))
                ents = f.get("entity_keys", []) or []
                if ents:
                    st.markdown("**Entities:** " + ", ".join(ents))
                    if st.button("Open first entity in Explorer",
                                 key=f"fd_open_{f.get('finding_id')}"):
                        st.session_state.as_selected_entity = ents[0]
                        state.set_screen(state.EXPLORER)
            with c2:
                st.markdown(f"ID: `{f.get('finding_id')}`")
                st.markdown(f"Confidence: {f.get('confidence')}")
                st.markdown(f"Status: **{f.get('status')}**"
                            + ("  \n(read-only in this UI)" if read_only
                               else ""))
                st.markdown(f"Detector: `{f.get('detector', '')}`")
                for p in f.get("provenance") or []:
                    if isinstance(p, dict) and p.get("document_name"):
                        st.caption("· " + fmt_provenance(
                            p.get("document_name"), p.get("version_label"),
                            p.get("section_no"), p.get("section_title"),
                            p.get("page_start"), p.get("page_end"),
                            p.get("source_chunk_id")))
                for ev in (f.get("evidence") or [])[:6]:
                    if not isinstance(ev, dict):
                        continue
                    head = (f"{ev.get('kind', 'evidence')}: "
                            f"{ev.get('key', '')}")
                    note = ev.get("note") or ""
                    if note:
                        head += f" — {note}"
                    st.caption("· " + head)
                    p = ev.get("provenance") or {}
                    if p.get("document_name") or p.get("section_no"):
                        st.caption("  " + fmt_provenance(
                            p.get("document_name"), p.get("version_label"),
                            p.get("section_no"), p.get("section_title"),
                            p.get("page_start"), p.get("page_end"),
                            p.get("source_chunk_id")))
