"""M9 tests (part 3): app-service routing + AppTest UI smoke over the real app.

Service tests are UI-independent (explicit session/engine isolation where
possible); AppTest tests run the REAL ``app/main.py`` script headlessly and
drive the sidebar navigation buttons — the same browser entry point a user
clicks, without the browser.
"""

from __future__ import annotations

import pytest

streamlit = pytest.importorskip("streamlit", reason="M8/M9 app not installed")


# ---------------------------------------------------------------------------
# service-layer routing
# ---------------------------------------------------------------------------

class TestProfileRouting:

    def test_profile_of_version_abc(self):
        from app.services.m9_services import profile_of_version

        assert profile_of_version("1.0.0") == "application_hld"
        assert profile_of_version("1.1.0") == "application_hld"

    def test_compare_gate_rejects_same_version(self):
        from app.services import ServiceError
        from app.services.m9_services import compare_gate

        with pytest.raises(ServiceError, match="identical"):
            compare_gate("1.0.0", "1.0.0")

    def test_compare_gate_rejects_unknown_version(self):
        from app.services import ServiceError
        from app.services.m9_services import compare_gate

        with pytest.raises(ServiceError, match="not in the registry"):
            compare_gate("1.0.0", "v9998.8.8")

    def test_compare_gate_allows_abc_pair(self):
        from app.services.m9_services import compare_gate

        compare_gate("1.0.0", "1.1.0")  # must not raise

    def test_get_versions_profiled_shape(self):
        from app.services.m9_services import get_versions_profiled

        versions = get_versions_profiled()
        assert versions, "demo corpus must be registered in the project DB"
        assert {"profile", "profile_label", "structured", "is_upload",
                "detection", "timings_ms"} <= set(versions[0])
        abc = {v["version"]: v for v in versions}
        assert abc["1.0.0"]["profile"] == "application_hld"
        assert abc["1.0.0"]["structured"] is True

    def test_generic_profile_findings_unavailable(self, monkeypatch):
        """Generic profile -> honest unavailable marker, no detector run."""
        from app.services import m9_services

        monkeypatch.setattr(m9_services, "profile_of_version",
                            lambda v, session=None: "generic")
        out = m9_services.analyze_findings_profiled("whatever")
        assert out["unavailable"] is True
        assert out["findings"] == []
        assert "fabricat" in out["note"].lower()


# ---------------------------------------------------------------------------
# upload workflow through the service layer (isolated engine monkeypatching)
# ---------------------------------------------------------------------------

def _tiny_pdf(title: str) -> bytes:
    """A small but REAL text PDF: enough extractable text to pass M1's
    scan-candidate threshold (SPARSE_THRESHOLD = 40 chars/page) and the
    M2 chunker's minimum."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    body = (title + "\n") * 6
    page.insert_text((72, 72), body)
    data = doc.tobytes()
    doc.close()
    return data


class TestUploadWorkflow:

    @pytest.fixture()
    def isolated(self, tmp_path, monkeypatch):
        """Isolate uploads dir + project DB.

        The DB patch must hit ``backend.config.DB_PATH`` (the value the
        pipeline resolves through ``make_engine``) — creating an engine on a
        temp path is NOT enough, because the session-less pipeline path and
        the storage helpers construct their own engines from config.
        """
        from backend.storage.database import init_schema, make_engine

        db = tmp_path / "registry.db"
        monkeypatch.setattr("backend.config.DB_PATH", db)
        engine = make_engine(db)
        init_schema(engine)
        monkeypatch.setattr("backend.uploads.storage.UPLOADS_DIR",
                            tmp_path / "uploads")
        # uploads processed-JSON area isolated as well (save_result writes
        # there before the chunker runs)
        monkeypatch.setattr("backend.uploads.pipeline.UPLOADS_PROCESSED_DIR",
                            tmp_path / "uploads" / "processed")
        return db, engine

    def test_upload_generic_pdf_end_to_end(self, isolated):
        """A generic technical PDF: full pipeline runs, structured analysis
        honestly unavailable, Copilot-retrieval row registered."""
        from backend.storage.database import get_session
        from backend.uploads.pipeline import process_upload

        session = get_session(isolated[1])
        try:
            res = process_upload(_tiny_pdf("Quarterly financial review with "
                                           "accounting notes."), "finance.pdf",
                                 session=session)
        finally:
            session.close()
        d = res.to_dict()
        assert d["ok"] is True
        assert d["profile"] == "generic"
        assert d["structured"] is False
        assert d["entity_count"] == 0 and d["fact_count"] == 0
        assert d["version_label"] == "unversioned"
        assert d["page_count"] >= 1 and d["chunk_count"] >= 1
        assert "ingest_ms" in d["timings_ms"]

    def test_duplicate_upload_short_circuits(self, isolated):
        from backend.storage.database import get_session
        from backend.uploads.pipeline import process_upload

        data = _tiny_pdf("Same doc twice")  # SAME bytes (PDF embeds a
        # creation timestamp, so the helper must be called only once)
        session = get_session(isolated[1])
        try:
            first = process_upload(data, "a.pdf",
                                   session=session).to_dict()
            second = process_upload(data, "b.pdf",
                                    session=session).to_dict()
        finally:
            session.close()
        assert first["ok"] and second["ok"]
        assert second["duplicate"] is True
        assert second["version_label"] == first["version_label"]
        assert any(i.get("kind") == "duplicate" for i in second["issues"])

    def test_manual_profile_override_recorded(self, isolated):
        from backend.storage.database import get_session
        from backend.uploads.pipeline import process_upload

        session = get_session(isolated[1])
        try:
            res = process_upload(_tiny_pdf("Completely generic content."),
                                 "over.pdf", manual_profile="application_hld",
                                 session=session).to_dict()
        finally:
            session.close()
        assert res["detection"]["manual"] is True
        assert res["detection"]["profile"] == "application_hld"

    def test_unversioned_labels_stay_unique(self, isolated):
        """Two DIFFERENT unversioned uploads must not share the label
        ``unversioned`` — label-based lookups would cross documents."""
        from backend.storage.database import get_session
        from backend.uploads.pipeline import process_upload

        session = get_session(isolated[1])
        try:
            a = process_upload(_tiny_pdf("First unrelated document."),
                               "first.pdf", session=session).to_dict()
            b = process_upload(_tiny_pdf("Second unrelated document, "
                                         "different text."), "second.pdf",
                               session=session).to_dict()
        finally:
            session.close()
        assert a["version_label"] == "unversioned"
        assert b["version_label"] != a["version_label"]


# ---------------------------------------------------------------------------
# UI-independent screen helpers
# ---------------------------------------------------------------------------

class TestScreenHelpers:

    def test_profile_badges(self):
        from app.components import profile_badge

        assert "AUTOSAR" in profile_badge("autosar_adaptive_platform")
        assert "HLD" in profile_badge("application_hld")
        assert profile_badge("generic") != profile_badge("unknown-thing")

    def test_navigation_includes_upload(self):
        from app.components import SCREENS

        assert any(sid == "upload" for sid, _ in SCREENS)

    def test_state_defaults_include_uploads(self, monkeypatch):
        import app.state as app_state

        fake = type("S", (dict,), {
            "__getattr__": lambda self, k: self[k],
            "__setattr__": lambda self, k, v: self.__setitem__(k, v),
        })()

        class ST:
            session_state = fake

        monkeypatch.setattr(app_state, "st", ST())
        app_state.init_state()
        assert fake["as_last_uploads"] is None
        app_state.set_screen(app_state.UPLOAD)
        assert fake["as_screen"] == app_state.UPLOAD


# ---------------------------------------------------------------------------
# AppTest smoke over the real app (headless; no browser, no network)
# ---------------------------------------------------------------------------

class TestAppTestSmoke:

    @pytest.fixture(scope="class")
    def app_runner(self):
        from pathlib import Path

        from streamlit.testing.v1 import AppTest

        script = Path(__file__).resolve().parents[1] / "app" / "main.py"
        at = AppTest.from_file(str(script), default_timeout=120)
        at.run()
        assert not at.exception, at.exception
        return at

    def _nav(self, at, screen: str):
        btn = next(b for b in at.sidebar.button
                   if b.key == f"nav_{screen}")
        btn.click()
        at.run()
        assert not at.exception

    def test_dashboard_renders(self, app_runner):
        at = app_runner
        assert at.title[0].value == "Dashboard"

    def test_all_eight_screens_render(self, app_runner):
        at = app_runner
        for screen in ("upload", "workspace", "explorer", "copilot",
                       "findings", "compare", "export"):
            self._nav(at, screen)

    def test_upload_screen_widgets(self, app_runner):
        at = app_runner
        self._nav(at, "upload")
        # the explicit-process rule: file picker present, process button
        # exists and is DISABLED until files are staged
        assert any(u.key == "up_picker" for u in at.file_uploader)
        btn = next(b for b in at.button if b.key == "up_process")
        assert btn.disabled is True

    def test_workspace_default_version(self, app_runner):
        at = app_runner
        self._nav(at, "workspace")
        doc_box = next((sb for sb in at.selectbox
                        if sb.label == "Document / version"), None)
        assert doc_box is not None
        assert "ABC_HLD" in doc_box.value

    def test_compare_screen_version_options(self, app_runner):
        at = app_runner
        self._nav(at, "compare")
        keys = {sb.key for sb in at.selectbox}
        assert "rc_base" in keys and "rc_target" in keys
