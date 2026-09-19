"""Revision Compare screen (M8.13/M8.14): M7 comparator + impact analysis.

The UI collects base/target/depth and renders the structured
``RevisionComparison``; all diff/impact logic stays in the backend. Impacts
are consistently labelled as *potentially* impacted (task rule 13/32).
"""

from __future__ import annotations

import streamlit as st

from app import state
from app.components import fmt_provenance, profile_badge
from app.services import (ServiceError, compare_revisions_gated,
                          get_versions_profiled)


def render() -> None:
    try:
        versions = [v for v in get_versions_profiled() if v["has_registry"]]
    except ServiceError as exc:
        st.error(f"Database unavailable: {exc}")
        return
    if len(versions) < 1:
        st.warning(
            "No structured architecture available. Run the M4 extraction "
            "pipeline first (scripts/extract_entities.py).")
        return

    labels = [f"{v['document_name']} — v{v['version']}" for v in versions]
    vlabels = [v["version"] for v in versions]
    profiles = {v["version"]: v["profile"] for v in versions}

    # M9 honesty (Part K/N): comparison is only offered between versions of
    # the same structured profile; the gate below enforces it deterministically.
    st.caption("Comparisons run only between two versions of the same "
               "structured profile (e.g. two revisions of the ABC HLD). "
               "Unrelated or generic documents are never compared.")

    cc = st.columns([2, 2, 1, 1.6])
    with cc[0]:
        base = st.selectbox("Base version", vlabels,
                            index=_idx(vlabels,
                                       st.session_state.as_base_version),
                            key="rc_base")
    with cc[1]:
        tgt = st.selectbox("Target version", vlabels,
                           index=_idx(vlabels,
                                      st.session_state.as_target_version),
                           key="rc_target")
    with cc[2]:
        depth = st.number_input("Impact depth", 0, 3,
                                value=int(st.session_state.as_compare_depth),
                                key="rc_depth")
    with cc[3]:
        st.write("")
        persist = st.checkbox("Persist comparison", key="rc_persist",
                              help="Store the comparison through the M7 "
                                   "idempotent CompareRun persistence.")

    st.session_state.as_base_version = base
    st.session_state.as_target_version = tgt
    st.session_state.as_compare_depth = int(depth)

    if base == tgt:
        st.warning("Base and target versions must differ.")
        return
    if profiles.get(base) != profiles.get(target):
        st.warning(
            f"Profiles differ: {profile_badge(profiles.get(base, 'generic'))} "
            f"vs {profile_badge(profiles.get(target, 'generic'))}. Comparison "
            "requires two revisions of the same logical project/schema.")
        return

    if st.button("Compare revisions", type="primary", key="rc_run"):
        with st.spinner("Comparing revisions…"):
            try:
                st.session_state.as_last_comparison = compare_revisions_gated(
                    base, tgt, depth=int(depth), persist=persist)
            except ServiceError as exc:
                st.session_state.as_last_comparison = None
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001 - UI guard
                st.session_state.as_last_comparison = None
                st.error(f"Comparison failed: {exc}")

    _render(st.session_state.as_last_comparison)


def _idx(labels: list[str], value: str | None) -> int:
    if value in labels:
        return labels.index(value)
    return 0 if len(labels) == 1 else min(1, len(labels) - 1)


def _render(comp: dict | None) -> None:
    if comp is None:
        st.info("Select two versions and run the comparison.")
        return

    s = comp.get("summary", {})
    st.subheader("Summary")
    m = st.columns(4)
    m[0].metric("Entities added", s.get("entity_added", 0))
    m[1].metric("Entities removed", s.get("entity_removed", 0))
    m[2].metric("Entities changed", s.get("entity_changed", 0))
    m[3].metric("Relationships ±",
                f"+{s.get('relationship_added', 0)} / "
                f"-{s.get('relationship_removed', 0)}")
    m2 = st.columns(3)
    m2[0].metric("Potentially impacted", s.get("impact_count", 0))
    m2[1].metric("Revision findings", s.get("revision_finding_count", 0))
    m2[2].metric("Validation", "ok" if comp.get("validation", {})
                 .get("errors") == [] else "see below")

    errs = comp.get("validation", {}).get("errors", [])
    if errs:
        st.error("Validation errors: " + "; ".join(map(str, errs[:8])))

    st.subheader("Entity changes")
    _entity_table(comp.get("entity_changes", []))

    st.subheader("Relationship changes")
    _rel_table(comp.get("relationship_changes", []))

    st.subheader("Potentially impacted")
    st.caption("Impact = deterministic graph neighborhood of a change. "
               "These are *potential* impacts, not confirmed defects.")
    _impact_table(comp.get("impacts", []))

    st.subheader("Revision findings")
    _finding_table(comp.get("revision_findings", []))


def _prov_line(prov_list: list[dict]) -> str:
    for p in prov_list or []:
        line = fmt_provenance(
            p.get("document_name"), p.get("version_label"),
            p.get("section_no"), p.get("section_title"),
            p.get("page_start"), p.get("page_end"),
            p.get("source_chunk_id"))
        if line:
            return line
    return "—"


def _entity_table(changes: list[dict]) -> None:
    if not changes:
        st.caption("No entity changes.")
        return
    rows = [{
        "change": c.get("change_type"),
        "type": c.get("entity_type"),
        "entity": c.get("entity_key"),
        "name": c.get("display_name"),
        "confidence": c.get("confidence"),
        "provenance": _prov_line(c.get("provenance")),
    } for c in changes]
    st.dataframe(rows, use_container_width=True, height=240)


def _rel_table(changes: list[dict]) -> None:
    if not changes:
        st.caption("No relationship changes.")
        return
    rows = [{
        "change": c.get("change_type"),
        "subject": c.get("subject"),
        "predicate": c.get("predicate"),
        "object": c.get("object"),
        "confidence": c.get("confidence"),
        "provenance": _prov_line(c.get("provenance")),
    } for c in changes]
    st.dataframe(rows, use_container_width=True, height=280)


def _impact_table(impacts: list[dict]) -> None:
    if not impacts:
        st.caption("No potential impacts at this depth.")
        return
    for i in impacts:
        path = "  →  ".join(
            step.get("frm", "?") + " --" + step.get("predicate", "?") + "→ "
            + step.get("to", "?")
            if step.get("direction", "forward") == "forward"
            else step.get("frm", "?") + " <--" + step.get("predicate", "?")
            + "-- " + step.get("to", "?")
            for step in i.get("path", []))
        header = (f"{i.get('impacted_entity_key')} "
                  f"[{i.get('category')}] depth {i.get('depth')}")
        with st.expander(header):
            st.markdown(f"**Reason:** {i.get('reason')}")
            st.markdown(f"**Source change:** `{i.get('source_change_id')}`")
            if path:
                st.markdown(f"**Path:** {path}")
            st.caption(_prov_line(i.get("provenance", [])))


def _finding_table(findings: list[dict]) -> None:
    if not findings:
        st.caption("No revision-level findings (changes are not "
                   "automatically defects).")
        return
    for f in findings:
        header = (f"[{f.get('severity')}] {f.get('finding_type')} — "
                  f"{f.get('title', '')}")
        with st.expander(header):
            st.markdown(f.get("description", ""))
            if f.get("entity_keys"):
                st.markdown("**Entities:** "
                            + ", ".join(f["entity_keys"]))
            for p in f.get("provenance", []):
                st.caption(_prov_line([p]))
