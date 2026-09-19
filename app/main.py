"""ArchSense application entry point (M8.32).

Run with::

    ./.venv/Scripts/python.exe -m streamlit run app/main.py

Single-page router: the sidebar selects the screen; ``app.state`` carries the
shared workspace context (version, selections) across screens (M8.3/M8.19).
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import state  # noqa: E402
from app.components import SCREENS  # noqa: E402

st.set_page_config(
    page_title="ArchSense",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _render(screen: str) -> None:
    # imported lazily so every screen stays independent of the others
    if screen == state.DASHBOARD:
        from app.screens import dashboard
        dashboard.render()
    elif screen == state.UPLOAD:
        from app.screens import upload
        upload.render()
    elif screen == state.WORKSPACE:
        from app.screens import document_workspace
        document_workspace.render()
    elif screen == state.EXPLORER:
        from app.screens import architecture_explorer
        architecture_explorer.render()
    elif screen == state.COPILOT:
        from app.screens import copilot
        copilot.render()
    elif screen == state.FINDINGS:
        from app.screens import findings
        findings.render()
    elif screen == state.COMPARE:
        from app.screens import revision_compare
        revision_compare.render()
    elif screen == state.EXPORT:
        from app.screens import export_report
        export_report.render()


def main() -> None:
    state.init_state()

    with st.sidebar:
        st.markdown("### 📐 ArchSense")
        st.caption("AUTOSAR HLD analysis workspace — grounded Q&A, "
                   "structured architecture, deterministic findings and "
                   "revision comparison.")
        labels = {sid: label for sid, label in SCREENS}
        current = st.session_state.as_screen
        for sid, label in SCREENS:
            if st.button(label,
                         use_container_width=True,
                         type="primary" if sid == current else "secondary",
                         key=f"nav_{sid}"):
                state.set_screen(sid)
        st.divider()
        if st.session_state.get("as_version"):
            st.caption(f"Active version: **v{st.session_state.as_version}**")

    screen = st.session_state.as_screen
    title = dict(SCREENS).get(screen, screen)
    st.title(title)
    _render(screen)
    # In-page navigation buttons (dashboard cards, "Open in Explorer" links)
    # change as_screen during rendering; re-route immediately so one click
    # navigates without requiring a second interaction.
    if st.session_state.as_screen != screen:
        st.rerun()


if __name__ == "__main__":
    main()
