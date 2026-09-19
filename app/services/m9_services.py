"""M9 application services: upload workflow + profile-aware analysis routing.

Thin adapters only — every function delegates to an existing backend
subsystem (M9 uploads pipeline / M4 / M5 / M6 / M7). UI-independent and
unit-testable (explicit ``session`` for tests). Caching policy follows the
M8 rules (D-042): resources are cached per process; analysis results are
NEVER cached.

Profile-aware routing (D-048/D-049):

- ``analyze_findings`` selects the detector set from the version's profile
  (ABC suite / AUTOSAR suite / none for generic).
- ``get_upload_vector_store`` / ``ask_copilot_for_document`` route retrieval
  to the ISOLATED upload collection so Copilot works over uploaded documents
  without touching the main corpus index.
- ``compare_revisions`` refuses comparisons whose two versions do not share
  a compatible structured profile (never fake a diff).
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from app.services.app_services import ServiceError, get_versions, new_session
from backend.config import VECTORS_DIR, UPLOAD_COLLECTION
from backend.uploads.profiles import (PROFILE_AUTOSAR, PROFILE_GENERIC,
                                      PROFILE_APPLICATION_HLD, get_profile,
                                      profile_name)


# ---------------------------------------------------------------------------
# profile plumbing
# ---------------------------------------------------------------------------

_PROFILE_BY_DOC_TYPE = {
    "HLD": PROFILE_APPLICATION_HLD.name,
    "AUTOSAR_AP": PROFILE_AUTOSAR.name,
    "GENERIC": PROFILE_GENERIC.name,
}


def profile_of_version(version_label: str, session=None) -> str:
    """The profile name recorded for a version (stats_json); fall back to
    doc_type inference for pre-M9 versions (ABC synthetic = application_hld).
    """
    own = session is None
    session = session or new_session()
    try:
        from sqlalchemy import select
        from backend.storage.models import Document, DocumentVersion

        row = (session.query(DocumentVersion, Document)
               .join(Document, DocumentVersion.document_id == Document.id)
               .filter(DocumentVersion.version_label == version_label)
               .first())
        if row is None:
            return PROFILE_GENERIC.name
        ver, doc = row
        stats = ver.stats_json or {}
        stored = (stats.get("upload") or {}).get("detection", {}).get("profile")
        if stored:
            return profile_name(stored)
        return _PROFILE_BY_DOC_TYPE.get(doc.doc_type, PROFILE_GENERIC.name)
    finally:
        if own:
            session.close()


# ---------------------------------------------------------------------------
# upload workflow (explicit user action only)
# ---------------------------------------------------------------------------


def process_uploads(files: list[tuple[bytes, str]],
                    manual_profile: str | None = None,
                    ) -> list[dict]:
    """Run the M9 pipeline for each (bytes, name) pair. NEVER cached.

    Returns per-file result dicts (UploadPipelineResult.to_dict()); failures
    are reported per file, not raised past the UI boundary.
    """
    from backend.uploads.pipeline import process_upload

    out: list[dict] = []
    for data, name in files:
        try:
            res = process_upload(data, name, manual_profile=manual_profile)
            out.append(res.to_dict())
        except Exception as exc:  # noqa: BLE001 - per-file error reporting
            out.append({
                "original_name": name,
                "ok": False,
                "error": str(exc),
            })
    _clear_document_caches()
    return out


@st.cache_resource(show_spinner=False)
def get_upload_vector_store():
    """The ISOLATED upload Chroma collection (main corpus untouched)."""
    from backend.rag.vector_store import get_vector_store

    try:
        return get_vector_store(collection_name=UPLOAD_COLLECTION,
                                persist_dir=VECTORS_DIR / "chroma")
    except Exception as exc:  # pragma: no cover - chroma init failure
        raise ServiceError(
            f"Upload vector index unavailable: {exc}") from exc


@st.cache_resource(show_spinner=False)
def get_upload_copilot(provider: str | None = None):
    """RAGCopilot wired to the upload collection (M3 stack, M9 store)."""
    from backend.rag.copilot import RAGCopilot
    from backend.rag.embedder import get_embedder
    from backend.rag.hybrid import get_hybrid_service
    from backend.rag.llm.factory import get_llm_provider

    try:
        retriever = get_hybrid_service(embedder=get_embedder(),
                                       store=get_upload_vector_store())
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError(
            f"Upload retrieval unavailable: {exc}") from exc
    try:
        llm = get_llm_provider(provider)
    except Exception as exc:
        raise ServiceError(str(exc)) from exc
    return RAGCopilot(retriever=retriever, llm=llm, top_k=5)


def ask_copilot_for_document(question: str, version: str | None = None,
                             top_k: int = 5,
                             provider: str | None = None) -> dict:
    """Copilot over the upload collection (document-filtered).

    Uses the version label as the retrieval filter — uploaded documents keep
    their honest version label ("unversioned" when none was detected).
    """
    from app.services.app_services import ask_copilot as ask_main
    try:
        copilot = get_upload_copilot(provider=provider)
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError(str(exc)) from exc
    copilot.top_k = top_k
    filters = {"version": version} if version else None
    answer = copilot.ask(question, filters=filters)
    return answer.to_dict()


def _clear_document_caches() -> None:
    """Invalidate cached data snapshots after the document set changes."""
    try:
        from app.services import app_services as svc
        svc._processed_sections.cache_clear()
    except Exception:  # noqa: BLE001 - cache clearing is best-effort
        pass


# ---------------------------------------------------------------------------
# profile-aware analysis routing
# ---------------------------------------------------------------------------


def analyze_findings_profiled(version_label: str,
                              persist: bool = False) -> dict:
    """Run M6 with the detector set matching the version's profile.

    Generic profile: returns an honest unavailable marker (never fake
    findings) instead of running inappropriate ABC detectors.
    """
    profile = profile_of_version(version_label)
    if profile == PROFILE_GENERIC.name:
        return {
            "version": version_label,
            "profile": profile,
            "unavailable": True,
            "findings": [],
            "by_type": {}, "by_severity": {}, "by_status": {},
            "note": ("No structured registry for this document profile; "
                     "findings analysis is unavailable (M1/M2/M3 analysis "
                     "only). Nothing was fabricated."),
        }
    from app.services.app_services import analyze_findings
    result = analyze_findings(version_label, persist=persist)
    result["profile"] = profile
    return result


def compare_gate(base_version: str, target_version: str) -> None:
    """Deterministic revision-compare gate (task Part K/N).

    Raises ``ServiceError`` when the pair is not comparable:
    - either version missing from the registry,
    - identical versions (M7 rule, surfaced early with a clear message),
    - profiles incompatible (only application_hld pairs — and, once a second
      compatible AUTOSAR release exists, matching autosar pairs — are
      structured-comparable; generic documents are never compared).
    """
    if base_version == target_version:
        raise ServiceError(
            "Base and target versions are identical; revision comparison "
            "requires two distinct versions.")
    session = new_session()
    try:
        from backend.storage.models import Document, DocumentVersion

        rows = (session.query(DocumentVersion, Document)
                .join(Document, DocumentVersion.document_id == Document.id)
                .filter(DocumentVersion.version_label.in_(
                    [base_version, target_version]))
                .all())
        found = {ver.version_label: (ver, doc) for ver, doc in rows}
        for v in (base_version, target_version):
            if v not in found:
                raise ServiceError(
                    f"Version '{v}' is not in the registry. Process the "
                    f"document first (Upload screen or M1/M4 pipeline).")
        pb = profile_of_version(base_version, session=session)
        pt = profile_of_version(target_version, session=session)
        if pb != pt:
            raise ServiceError(
                f"Profiles differ ({pb!r} vs {pt!r}): comparison requires "
                f"two revisions of the same logical project/schema.")
        if pb == PROFILE_GENERIC.name:
            raise ServiceError(
                "Generic documents have no structured registry, so there is "
                "nothing to compare (Copilot-only analysis).")
        if pb == PROFILE_AUTOSAR.name:
            # permitted for matching AUTOSAR releases once a second one is
            # uploaded; only one exists today (honest gate, Part N)
            docs = {found[v][1].sha256
                    for v in (base_version, target_version)}
            if len(docs) == 1:
                raise ServiceError(
                    "Base and target resolve to the same document content; "
                    "a second compatible AUTOSAR release is required for "
                    "revision comparison.")
    finally:
        session.close()


def compare_revisions_gated(base_version: str, target_version: str,
                            depth: int = 1, persist: bool = False) -> dict:
    """M7 comparison behind the profile gate (see :func:`compare_gate`)."""
    compare_gate(base_version, target_version)
    from app.services.app_services import compare_revisions
    return compare_revisions(base_version, target_version, depth=depth,
                             persist=persist)


# ---------------------------------------------------------------------------
# document listing with profile info
# ---------------------------------------------------------------------------


def get_versions_profiled(session=None) -> list[dict]:
    """``get_versions`` enriched with per-version profile + provenance of
    the upload pipeline (detection evidence, timings)."""
    versions = get_versions(session=session)
    own = session is None
    session = session or new_session()
    try:
        from backend.storage.models import Document, DocumentVersion

        rows = (session.query(DocumentVersion, Document)
                .join(Document, DocumentVersion.document_id == Document.id)
                .all())
        stats_by_version = {ver.version_label: (ver.stats_json or {})
                            for ver, _doc in rows}
        sha_by_version = {ver.version_label: doc.sha256
                          for ver, doc in rows}
        for v in versions:
            stats = stats_by_version.get(v["version"], {})
            upload = stats.get("upload") or {}
            v["profile"] = profile_of_version(v["version"], session=session)
            v["profile_label"] = get_profile(v["profile"]).label
            v["structured"] = get_profile(v["profile"]).structured
            v["is_upload"] = bool(upload)
            v["sha256"] = sha_by_version.get(v["version"])
            v["detection"] = upload.get("detection", {})
            v["timings_ms"] = upload.get("timings_ms", {})
        return versions
    finally:
        if own:
            session.close()


__all__ = [
    "profile_of_version", "process_uploads", "get_upload_vector_store",
    "get_upload_copilot", "ask_copilot_for_document",
    "analyze_findings_profiled", "compare_gate", "compare_revisions_gated",
    "get_versions_profiled",
]
