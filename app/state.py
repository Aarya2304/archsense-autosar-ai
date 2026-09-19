"""Session state for the ArchSense UI (M8.19).

All cross-screen selections live in ``st.session_state`` under the ``as_``
prefix. ``init_state()`` is called on every rerun from ``main.py`` and only
fills keys that are missing, so user choices survive navigation.

``set_version()`` implements the stale-selection rule (D-043): switching the
version clears selections that may not exist under the new version (selected
entity, selected finding, current comparison, current report selections) —
the UI must never display data from a different version context.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

# screen ids used by navigation.py / set_screen()
DASHBOARD = "dashboard"
UPLOAD = "upload"
WORKSPACE = "workspace"
EXPLORER = "explorer"
COPILOT = "copilot"
FINDINGS = "findings"
COMPARE = "compare"
EXPORT = "export"

_SCREENS = (DASHBOARD, UPLOAD, WORKSPACE, EXPLORER, COPILOT, FINDINGS,
            COMPARE, EXPORT)

# (key, default) pairs initialised on first run.
_DEFAULTS: list[tuple[str, Any]] = [
    ("as_screen", DASHBOARD),
    # workspace / explorer / copilot / findings share one version choice
    ("as_version", None),            # version label, e.g. "1.1.0"
    # document workspace
    ("as_page", 1),
    ("as_section", None),            # section_no of the selected section
    # explorer
    ("as_selected_entity", None),    # canonical entity key
    ("as_entity_type", None),        # entity-type filter
    ("as_predicate", None),          # predicate filter
    ("as_min_conf", 0.0),            # minimum-confidence filter
    ("as_search", ""),               # entity search text
    ("as_depth", 1),                 # related-neighborhood depth
    # copilot
    ("as_question", ""),
    ("as_retrieval_mode", "hybrid"),
    ("as_top_k", 5),
    ("as_last_answer", None),        # CopilotAnswer.to_dict() of last ask
    # findings
    ("as_severity_filter", None),
    ("as_finding_type_filter", None),
    ("as_status_filter", None),
    ("as_last_findings", None),      # findings summary of last analysis
    # revision compare
    ("as_base_version", None),
    ("as_target_version", None),
    ("as_compare_depth", 1),
    ("as_last_comparison", None),    # RevisionComparison.to_dict()
    # export
    ("as_report_include", {
        "architecture": True,
        "findings": True,
        "comparison": True,
        "evidence": True,
    }),
    # M9 upload workflow (explicit processing only; no upload state is
    # carried between reruns except the last results list for display)
    ("as_last_uploads", None),       # list of UploadPipelineResult dicts
]


def init_state() -> None:
    """Create every state key that does not exist yet (safe on every rerun)."""
    for key, default in _DEFAULTS:
        if key not in st.session_state:
            st.session_state[key] = default
    if st.session_state.as_screen not in _SCREENS:
        st.session_state.as_screen = DASHBOARD


def set_screen(screen: str) -> None:
    """Navigate to a screen id (used by buttons/cards across screens)."""
    if screen in _SCREENS:
        st.session_state.as_screen = screen


def set_version(version: str | None) -> None:
    """Select a version and invalidate stale per-version selections (D-043).

    Clearing is unconditional on change: entity keys, findings and comparison
    results are version-scoped, so after a switch no previous selection can be
    assumed valid. The workspace page/section resets to the first page.
    """
    if version == st.session_state.get("as_version"):
        return
    st.session_state.as_version = version
    st.session_state.as_page = 1
    st.session_state.as_section = None
    st.session_state.as_selected_entity = None
    st.session_state.as_last_findings = None
    st.session_state.as_last_comparison = None
    st.session_state.as_last_answer = None
    st.session_state.as_severity_filter = None
    st.session_state.as_finding_type_filter = None
    st.session_state.as_status_filter = None
    st.session_state["as_last_report"] = None  # report is version-paired
    # M9: the graph cache is keyed per version, so a new/changed upload only
    # needs the data snapshot invalidated (vector stores are collection-level
    # resources and are shared, not cached per document).
    try:
        from app.services.app_services import _processed_sections
        _processed_sections.cache_clear()
    except Exception:  # noqa: BLE001 - best effort
        pass
