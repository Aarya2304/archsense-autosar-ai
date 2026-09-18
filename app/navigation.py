"""Navigation helpers (M8): screen router keys and consistent headers.

``main.py`` maps the ``as_screen`` id to a page module's ``render()``.
``set_screen`` buttons across screens provide the dashboard action cards and
cross-links (e.g. "Open in Architecture Explorer" from findings).
"""

from __future__ import annotations

import streamlit as st

from app import state


def screen_title(screen: str) -> str:
    titles = {
        state.DASHBOARD: "Dashboard",
        state.WORKSPACE: "Document Workspace",
        state.EXPLORER: "Architecture Explorer",
        state.COPILOT: "Copilot",
        state.FINDINGS: "Findings",
        state.COMPARE: "Revision Compare",
        state.EXPORT: "Export & Report",
    }
    return titles.get(screen, screen)


def render_header(screen: str, subtitle: str | None = None) -> None:
    """Consistent page header with the workspace context on the right."""
    cols = st.columns([4, 2])
    with cols[0]:
        st.markdown(f"## {screen_title(screen)}")
        if subtitle:
            st.caption(subtitle)
    with cols[1]:
        if st.session_state.get("as_version"):
            st.markdown(
                f"<div style='text-align:right;padding-top:10px;color:var(--text-color);'>"
                f"Version: <strong>{st.session_state.as_version}</strong></div>",
                unsafe_allow_html=False,
            )
        else:
            st.caption("No version selected")


def go(screen: str, label: str, key: str | None = None,
       **button_kwargs) -> bool:
    """A button that navigates to ``screen`` when clicked."""
    return st.button(label, key=key or f"nav_{screen}_{label[:12]}",
                     **button_kwargs)
