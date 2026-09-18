"""M8 tests (part 1): session-state logic and UI-independent service logic.

Streamlit is imported but never runs a script server here; ``state.py`` and
the service helpers are plain functions over dictionaries, so they are tested
directly against a monkeypatched ``st.session_state``.
"""

from __future__ import annotations

import pytest

import app.state as app_state


class FakeSessionState(dict):
    """Minimal stand-in for st.session_state (attribute + item access)."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value

    def get(self, key, default=None):
        return dict.get(self, key, default)


@pytest.fixture()
def fake_state(monkeypatch):
    """Replace st.session_state with a plain dict-like object per test."""
    fake = FakeSessionState()
    monkeypatch.setattr(app_state.st, "session_state", fake, raising=False)
    fake.clear()
    app_state.init_state()
    return fake


# ------------------------------------------------------------------ state --


def test_init_state_creates_defaults(fake_state):
    assert fake_state["as_screen"] == app_state.DASHBOARD
    assert fake_state["as_version"] is None
    assert fake_state["as_top_k"] == 5
    assert fake_state["as_report_include"]["architecture"] is True


def test_init_state_is_idempotent(fake_state):
    fake_state["as_version"] = "1.1.0"
    fake_state["as_page"] = 7
    app_state.init_state()
    assert fake_state["as_version"] == "1.1.0"
    assert fake_state["as_page"] == 7


def test_set_screen_roundtrip(fake_state):
    app_state.set_screen(app_state.COMPARE)
    assert fake_state["as_screen"] == app_state.COMPARE
    app_state.set_screen("not-a-screen")
    assert fake_state["as_screen"] == app_state.COMPARE


def test_set_version_clears_stale_selections(fake_state):
    app_state.set_screen(app_state.EXPLORER)
    fake_state["as_selected_entity"] = "component:C-05"
    fake_state["as_page"] = 4
    fake_state["as_last_findings"] = {"findings": [1]}
    fake_state["as_last_comparison"] = {"summary": {}}
    fake_state["as_last_answer"] = {"status": "answered"}

    app_state.set_version("1.1.0")
    assert fake_state["as_version"] == "1.1.0"
    assert fake_state["as_selected_entity"] is None
    assert fake_state["as_page"] == 1
    assert fake_state["as_last_findings"] is None
    assert fake_state["as_last_comparison"] is None
    assert fake_state["as_last_answer"] is None
    # screen selection itself survives a version change
    assert fake_state["as_screen"] == app_state.EXPLORER


def test_set_version_noop_for_same_version(fake_state):
    app_state.set_version("1.0.0")
    fake_state["as_selected_entity"] = "component:C-02"
    fake_state["as_page"] = 9
    app_state.set_version("1.0.0")
    assert fake_state["as_selected_entity"] == "component:C-02"
    assert fake_state["as_page"] == 9


# -------------------------------------------------- filter / breakdown -----


def test_filter_findings_combinations():
    from app.services.app_services import filter_findings

    findings = [
        {"finding_type": "orphan_entity", "severity": "medium",
         "status": "open"},
        {"finding_type": "orphan_entity", "severity": "medium",
         "status": "accepted"},
        {"finding_type": "conflicting_providers", "severity": "high",
         "status": "open"},
        {"finding_type": "unconsumed_signal", "severity": "low",
         "status": "open"},
    ]
    assert len(filter_findings(findings)) == 4
    assert len(filter_findings(findings, severity="medium")) == 2
    assert (len(filter_findings(findings, severity="medium",
                                status="accepted")) == 1)
    assert (len(filter_findings(findings,
                                finding_type="conflicting_providers")) == 1)
    assert filter_findings(findings, severity="info") == []


def test_findings_breakdown_counts():
    from app.services.app_services import findings_breakdown

    breakdown = findings_breakdown([
        {"finding_type": "orphan_entity", "severity": "medium",
         "status": "open"},
        {"finding_type": "orphan_entity", "severity": "medium",
         "status": "open"},
        {"finding_type": "conflicting_providers", "severity": "high",
         "status": "accepted"},
    ])
    assert breakdown["by_type"] == {"conflicting_providers": 1,
                                    "orphan_entity": 2}
    assert breakdown["by_severity"] == {"high": 1, "medium": 2}
    assert breakdown["by_status"] == {"accepted": 1, "open": 2}


# -------------------------------------------------------- formatting -------


def test_fmt_provenance_full_and_partial():
    from app.components import fmt_provenance

    line = fmt_provenance("ABC_HLD_v1.1.0.pdf", "1.1.0", "4.1",
                          "DoorStatusIF (IF-01)", 9, 9, "bdbbc65aabcdef")
    assert "ABC_HLD_v1.1.0.pdf" in line
    assert "v1.1.0" in line
    assert "Section 4.1 DoorStatusIF (IF-01)" in line
    assert "p.9" in line
    assert "chunk bdbbc65a" in line

    single = fmt_provenance("D.pdf", "2.0", "1", "Intro", 3, 5)
    assert "pp.3-5" in single
    assert fmt_provenance(None) == ""


def test_provenance_from_dict_accepts_backend_keys():
    from app.components import provenance_from_dict

    line = provenance_from_dict({
        "document": "D.pdf", "version": "1.0.0", "section_no": "3.1",
        "section_title": "Overview", "page_start": 4, "page_end": 4,
        "source_chunk_id": "abc1234567890",
    })
    assert "D.pdf" in line and "Section 3.1 Overview" in line
    assert "p.4" in line and "chunk abc12345" in line


def test_esc_html_escapes_untrusted_text():
    from app.components import esc

    assert esc("<script>x</script>") == "&lt;script&gt;x&lt;/script&gt;"
    assert esc('a"b') == "a&quot;b"
