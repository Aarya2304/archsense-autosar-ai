"""M9 tests (part 1): upload storage security + evidence-based profiling.

Storage tests run against an isolated SQLite DB and a temp upload directory
(no real runtime artifacts are touched); the profile tests run against the
REAL synthetic processed JSON and the REAL external AUTOSAR processed JSON
(evidence-based detection must distinguish them, task Part S).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backend.uploads.profiles import (PROFILE_AUTOSAR, PROFILE_GENERIC,
                                      PROFILE_APPLICATION_HLD, detect_profile,
                                      get_profile)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _minimal_pdf_bytes(title: str = "Generic note") -> bytes:
    """A tiny REAL PDF (PyMuPDF-openable) generated on the fly."""
    import fitz  # PyMuPDF ships with the project (M1)

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), title)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture()
def upload_env(tmp_path, monkeypatch):
    """Isolated uploads area + dedicated SQLite DB for storage tests."""
    from backend.storage.database import get_session, init_schema, make_engine

    up_dir = tmp_path / "uploads"
    monkeypatch.setattr("backend.uploads.storage.UPLOADS_DIR", up_dir)
    engine = make_engine(tmp_path / "registry.db")
    init_schema(engine)
    session = get_session(engine)
    yield session, up_dir
    session.close()


# ---------------------------------------------------------------------------
# storage security (task Part R / Part S)
# ---------------------------------------------------------------------------

class TestUploadStorage:

    def test_valid_pdf_accepted(self, upload_env):
        session, up_dir = upload_env
        from backend.uploads.storage import store_upload

        data = _minimal_pdf_bytes("Doc A")
        up = store_upload(data, "Doc A.pdf", session=session)
        assert up.duplicate is False
        assert up.safe_name.endswith("Doc A.pdf")
        assert up.sha256 == hashlib.sha256(data).hexdigest()
        assert up.stored_path.is_file()
        assert up.stored_path.parent == up_dir
        assert up.stored_path.name.startswith(up.sha256[:8])

    def test_non_pdf_rejected(self, upload_env):
        session, _ = upload_env
        from backend.uploads.storage import UploadError, store_upload

        with pytest.raises(UploadError, match="PDF"):
            store_upload(b"not a pdf at all", "notes.txt", session=session)

    def test_renamed_non_pdf_rejected_by_magic_sniff(self, upload_env):
        session, _ = upload_env
        from backend.uploads.storage import UploadError, store_upload

        # a renamed .txt: correct extension, wrong magic bytes
        with pytest.raises(UploadError):
            store_upload(b"hello world content", "report.pdf",
                         session=session)

    def test_unsafe_filename_sanitized(self, upload_env):
        session, _ = upload_env
        from backend.uploads.storage import store_upload

        up = store_upload(_minimal_pdf_bytes(), "..\\..\\evil<>|name.pdf",
                          session=session)
        assert ".." not in up.safe_name
        assert "/" not in up.safe_name and "\\" not in up.safe_name
        assert "<" not in up.safe_name and ">" not in up.safe_name
        assert up.stored_path.parent.name != ""  # never escapes the dir

    def test_duplicate_content_deduped(self, upload_env):
        session, _ = upload_env
        from backend.uploads.storage import register_document, store_upload

        data = _minimal_pdf_bytes("Same bytes")
        first = store_upload(data, "first.pdf", session=session)
        register_document(session, first)          # pipeline step 2
        second = store_upload(data, "second_name.pdf", session=session)
        assert second.duplicate is True
        assert second.sha256 == first.sha256
        assert second.document_id == first.document_id
        assert second.notes  # dedupe is reported, not silent

    def test_oversize_rejected(self, upload_env, monkeypatch):
        session, _ = upload_env
        from backend.uploads.storage import UploadError, store_upload

        monkeypatch.setattr("backend.uploads.storage.MAX_UPLOAD_MB", 0)
        with pytest.raises(UploadError, match="size|large|MB"):
            store_upload(_minimal_pdf_bytes(), "big.pdf", session=session)


# ---------------------------------------------------------------------------
# evidence-based profile detection (task Part E / Part S)
# ---------------------------------------------------------------------------

class TestProfileDetection:

    def test_real_autosar_document_detected(self):
        path = Path("data/external_test/processed/"
                    "AUTOSAR_EXP_PlatformDesign__processed.json")
        if not path.is_file():
            pytest.skip("external AUTOSAR processed JSON not present")
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        pages = ["\n".join(pg["lines"]) for pg in data["pages"]]
        det = detect_profile(pages, data.get("tables", []),
                             data["document_name"])
        assert det.profile == PROFILE_AUTOSAR.name
        assert det.confidence > 0
        assert not det.manual
        assert len(det.signals_seen.get("autosar_adaptive_platform", [])) >= 2

    def test_synthetic_abc_hld_detected(self):
        path = Path("data/processed/ABC_HLD_v1.0.0__processed.json")
        if not path.is_file():
            pytest.skip("synthetic processed JSON not present")
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        pages = ["\n".join(pg["lines"]) for pg in data["pages"]]
        det = detect_profile(pages, data.get("tables", []),
                             data["document_name"])
        assert det.profile == PROFILE_APPLICATION_HLD.name

    def test_generic_pdf_is_generic(self):
        pages = ["Quarterly financial report.\nRevenue grew by 4% in Q2.",
                 "Appendix: accounting standards and audit notes."]
        det = detect_profile(pages, None, "finance_report.pdf")
        assert det.profile == PROFILE_GENERIC.name
        assert not det.structured

    def test_single_autosar_mention_is_not_enough(self):
        # task Part E: one sentence naming AUTOSAR must NOT yield the profile
        pages = ["This controller was inspired by AUTOSAR concepts.",
                 "The rest of the document discusses garden design."]
        det = detect_profile(pages, None, "garden_controller.pdf")
        assert det.profile == PROFILE_GENERIC.name

    def test_manual_override_recorded(self):
        det = detect_profile(["anything"], None, "x.pdf",
                             manual="application_hld")
        assert det.profile == PROFILE_APPLICATION_HLD.name
        assert det.manual is True
        assert det.confidence == 1.0

    def test_detection_dict_is_auditable(self):
        det = detect_profile(["AUTOSAR Runtime for Adaptive"], None, "x.pdf")
        d = det.to_dict()
        assert {"profile", "label", "structured_extraction", "confidence",
                "manual", "scores", "signals_seen"} <= set(d)

    def test_unknown_profile_name_falls_back_to_generic(self):
        assert get_profile("does-not-exist") is PROFILE_GENERIC
