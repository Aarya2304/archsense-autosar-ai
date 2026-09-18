"""Application services (M8.18): thin, cached adapters over M1-M7.

Every function here delegates to an existing backend subsystem — no backend
algorithm is duplicated in the UI layer. Caching policy (D-042):

- ``@st.cache_resource`` for process-wide immutable singletons (DB engine,
  session factory, vector store, hybrid retrieval service, copilot, graph).
- ``@st.cache_data`` for pure data snapshots (document summaries).
- NEVER cached: finding analysis runs, comparison runs, report generation
  (they write or reflect mutable DB state).

All functions are UI-independent and unit-testable: they accept an explicit
``session`` where tests need isolation. Backend failures surface as
``ServiceError`` with a user-actionable message.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import DB_PATH, PROCESSED_DIR  # noqa: E402
from backend.storage.database import (  # noqa: E402
    init_schema,
    make_engine,
    make_session_factory,
)
from backend.storage.models import (AnalysisRun,  # noqa: E402
                                    Document, DocumentVersion,
                                    ExtractionFact, Finding)

# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class ServiceError(RuntimeError):
    """A backend condition the UI should show as an actionable message."""


# ---------------------------------------------------------------------------
# process-wide singletons (cache_resource: one instance per process)
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def get_engine():
    """Cached SQLAlchemy engine for the project DB (schema ensured)."""
    engine = make_engine(DB_PATH)
    init_schema(engine)
    return engine


@st.cache_resource(show_spinner=False)
def get_session_factory():
    return make_session_factory(get_engine())


def new_session():
    """Open a short-lived session from the cached factory (caller closes)."""
    return get_session_factory()()


@st.cache_resource(show_spinner=False)
def _vector_store():
    try:
        from backend.rag.vector_store import get_vector_store
        return get_vector_store()
    except Exception as exc:  # pragma: no cover - chroma init failure
        raise ServiceError(
            f"Vector index unavailable: {exc}. Run the M2 indexing pipeline "
            "(scripts/build_index.py) first.") from exc


# ---------------------------------------------------------------------------
# document / workspace (M1)
# ---------------------------------------------------------------------------


def get_versions(session=None) -> list[dict]:
    """All document versions ordered by filename, then version label.

    Each dict carries the flags the UI uses to distinguish what is actually
    available: ``has_chunks`` (M1/M2 ingestion+index), ``has_registry`` (M4
    structured extraction) — the external AUTOSAR document, for example, has
    the former but not the latter (M8.25 honesty rule).
    """
    own = session is None
    session = session or new_session()
    try:
        rows = (
            session.query(DocumentVersion, Document)
            .join(Document, DocumentVersion.document_id == Document.id)
            .order_by(Document.filename, DocumentVersion.version_label)
            .all()
        )
        out: list[dict] = []
        for ver, doc in rows:
            out.append({
                "document_name": doc.filename,
                "version": ver.version_label,
                "version_id": ver.id,
                "status": (ver.status.value if hasattr(ver.status, "value")
                           else str(ver.status)),
                "page_count": ver.page_count,
                "chunk_count": ver.chunk_count,
                "has_chunks": ver.chunk_count > 0,
                "has_registry": session.query(ExtractionFact.id)
                    .filter(ExtractionFact.version_id == ver.id)
                    .first() is not None,
            })
        return out
    finally:
        if own:
            session.close()


@lru_cache(maxsize=8)
def _processed_sections(document_name: str) -> tuple[tuple[str, int], ...]:
    """(section_no, page) pairs from the M1 processed JSON (cached)."""
    stem = document_name.removesuffix(".pdf")
    path = PROCESSED_DIR / f"{stem}__processed.json"
    if not path.is_file():
        return ()
    data = json.loads(path.read_text(encoding="utf-8"))
    sections = data.get("sections", {})

    def _key(no: str) -> tuple:
        try:
            return tuple(int(p) for p in str(no).split("."))
        except ValueError:
            return (10 ** 6,)

    return tuple(sorted(((str(no), int(pg)) for no, pg in sections.items()),
                        key=lambda t: _key(t[0])))


def get_document_summary(version_label: str, session=None) -> dict | None:
    """Metadata + section/page map for the workspace (M1 data, cached)."""
    own = session is None
    session = session or new_session()
    try:
        row = (
            session.query(DocumentVersion, Document)
            .join(Document, DocumentVersion.document_id == Document.id)
            .filter(DocumentVersion.version_label == version_label)
            .first()
        )
        if row is None:
            return None
        ver, doc = row
        pages = list(range(1, (ver.page_count or 1) + 1))
        sections = _processed_sections(doc.filename)
        return {
            "document_name": doc.filename,
            "title": doc.title or doc.filename,
            "version": ver.version_label,
            "version_id": ver.id,
            "doc_type": doc.doc_type,
            "page_count": ver.page_count or len(pages),
            "pages": pages,
            "chunk_count": ver.chunk_count,
            "sections": [{"section_no": no, "page": pg} for no, pg in sections],
        }
    finally:
        if own:
            session.close()


def get_pdf_path(document_name: str) -> Path | None:
    """Locate the source PDF: the path M1 recorded, then sample dirs."""
    candidates = [
        PROCESSED_DIR.parent / "sample_docs" / document_name,
        PROCESSED_DIR.parent / "external_test" / document_name,
        PROCESSED_DIR.parent / document_name,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def get_page_text(document_name: str, version: str, page_no: int) -> str:
    """Concatenated text of the indexed chunks covering one page.

    Chunk text lives in the M2 Chroma index (the pipeline stores chunks
    there, not in the SQLite ``chunks`` table), so this reads the store's
    chunk metadata — the same trusted provenance the retriever uses.
    Returns "" when the index is unavailable (caller shows a note).
    """
    try:
        store = _vector_store()
    except ServiceError:
        return ""
    parts: list[str] = []
    for c in sorted(store.get_all_chunks(),
                    key=lambda c: (c.section_no, c.chunk_seq)):
        if (c.document_name == document_name
                and str(c.version) == str(version)
                and int(c.page_start) <= page_no <= int(c.page_end)):
            parts.append(c.text)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# architecture (M4 registry / M5 graph)
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def _build_graph(version_label: str):
    from backend.graph.service import GraphService

    service = GraphService(session_factory=get_session_factory())
    try:
        return service.build_graph(version=version_label)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc


def get_architecture_graph(version_label: str):
    """M5 MultiDiGraph + build stats for a version (cached per process)."""
    return _build_graph(version_label)


def get_architecture_stats(version_label: str) -> dict | None:
    """Entity/fact counts by type and predicate for one version."""
    graph, stats = get_architecture_graph(version_label)
    by_type: Counter = Counter(
        data.get("entity_type", "?") for _, data in graph.nodes(data=True))
    by_pred: Counter = Counter(
        data.get("predicate", "?") for _, _, data in graph.edges(data=True))
    return {
        "version": version_label,
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "entities_by_type": dict(sorted(by_type.items())),
        "facts_by_predicate": dict(sorted(by_pred.items())),
        "build_ms": getattr(stats, "build_time_ms", None),
    }


def get_entity_detail(version_label: str, entity_key: str) -> dict | None:
    """Full entity + relationship detail for the explorer side panel."""
    graph, _stats = get_architecture_graph(version_label)
    if entity_key not in graph.nodes:
        return None
    node = dict(graph.nodes[entity_key])
    outgoing, incoming = [], []
    for frm, to, key, data in graph.edges(keys=True, data=True):
        base = {
            "subject": frm, "predicate": data.get("predicate"), "object": to,
            "confidence": data.get("confidence"),
            "extractor": data.get("extractor"),
            "provenance": data.get("provenance", {}),
            "fact_key": key,
        }
        if frm == entity_key:
            outgoing.append(base)
        if to == entity_key:
            incoming.append(base)
    outgoing.sort(key=lambda e: (str(e["predicate"]), str(e["object"])))
    incoming.sort(key=lambda e: (str(e["subject"]), str(e["predicate"])))
    return {
        "entity_key": entity_key,
        "attributes": node,
        "outgoing": outgoing,
        "incoming": incoming,
    }


def search_entities(version_label: str, query: str) -> list[str]:
    """Canonical keys whose key/name contains ``query`` (case-insensitive)."""
    graph, _stats = get_architecture_graph(version_label)
    q = (query or "").strip().lower()
    hits = []
    for node, data in graph.nodes(data=True):
        hay = " ".join([str(node), str(data.get("name", "")),
                        str(data.get("normalized_name", ""))]).lower()
        if q in hay:
            hits.append(node)
    return sorted(hits)


# ---------------------------------------------------------------------------
# copilot (M2/M3)
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def get_copilot(provider: str | None = None):
    """Cached RAGCopilot wired exactly like scripts/ask_copilot.py (M3)."""
    from backend.rag.copilot import RAGCopilot
    from backend.rag.embedder import get_embedder
    from backend.rag.hybrid import get_hybrid_service
    from backend.rag.llm.factory import get_llm_provider
    from backend.rag.vector_store import get_vector_store

    try:
        retriever = get_hybrid_service(embedder=get_embedder(),
                                       store=get_vector_store())
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError(
            f"Retrieval services unavailable: {exc}. Run the M2 indexing "
            "pipeline first.") from exc
    try:
        llm = get_llm_provider(provider)
    except Exception as exc:
        raise ServiceError(str(exc)) from exc
    return RAGCopilot(retriever=retriever, llm=llm, top_k=5)


def ask_copilot(question: str, version: str | None = None,
                top_k: int = 5, provider: str | None = None) -> dict:
    """Run the M3 pipeline; return ``CopilotAnswer.to_dict()`` (never cached).

    Provider/API-key failures propagate as ``ServiceError`` with a
    user-actionable message; the answer's own structured failure states
    (insufficient evidence, validation failure) are returned as data.
    """
    try:
        copilot = get_copilot(provider=provider)
    except ServiceError:
        raise
    except Exception as exc:  # provider wiring failure -> actionable error
        raise ServiceError(str(exc)) from exc
    copilot.top_k = top_k
    filters = {"version": version} if version else None
    answer = copilot.ask(question, filters=filters)
    return answer.to_dict()


# ---------------------------------------------------------------------------
# findings (M6)
# ---------------------------------------------------------------------------


def analyze_findings(version_label: str, persist: bool = False) -> dict:
    """Run the M6 FindingEngine for a version (fresh run; never cached).

    With ``persist=True`` the results go through the M6 idempotent,
    review-preserving persistence (an explicit user action in the UI).
    """
    from backend.findings.engine import FindingEngine
    from backend.findings.persistence import persist_findings

    session = new_session()
    try:
        ver = session.query(DocumentVersion).filter(
            DocumentVersion.version_label == version_label).first()
        if ver is None:
            raise ServiceError(
                f"Version '{version_label}' not found in the registry.")
        engine = FindingEngine()
        result = engine.run(session, version_label)
        persist_stats = None
        if persist:
            persist_stats = persist_findings(session, ver.id, result.findings)
            session.commit()
        findings = []
        for f in result.findings:
            d = f.model_dump(mode="json") if hasattr(f, "model_dump") else dict(f)
            findings.append(d)
        return {
            "version": version_label,
            "findings": findings,
            "by_type": result.by_type(),
            "by_severity": result.by_severity(),
            "entity_count": result.entity_count,
            "fact_count": result.fact_count,
            "detectors_run": result.detectors_run,
            "validation_ok": result.validation_ok,
            "validation_issues": result.validation_issues,
            "timings_ms": result.timings_ms,
            "persist_stats": persist_stats,
        }
    finally:
        session.close()


def _enrich_finding_provenance(session, version_label: str,
                               findings: list[dict]) -> list[dict]:
    """M8 display adapter: fill empty evidence provenance from the registry.

    M6 persistence stores each evidence block's provenance only when the
    detector had one; graph-derived findings (e.g. ORPHAN_ENTITY) carry empty
    snapshots. Read-back resolves the finding's entity keys against the SAME
    M6 analysis context the detectors used and fills ONLY empty snapshots —
    existing M6 evidence is never overwritten and no new metadata is
    invented: every filled field comes from the trusted M4 registry row.
    """
    if not findings:
        return findings
    try:
        from backend.findings.context import (build_analysis_context,
                                              canonical_key,
                                              provenance_of_entity)
        ctx = build_analysis_context(session, version_label)
    except Exception:      # noqa: BLE001 - display-only enrichment
        return findings
    prov_by_key: dict[str, dict] = {}
    for k in {str(k) for f in findings for k in (f.get("entity_keys") or [])}:
        ckey = canonical_key(k)
        row = ctx.entities.get(ckey)
        if row is not None:
            p = provenance_of_entity(row)
            p["document_name"] = ctx.document_name
            p["version_label"] = ctx.version_label
            prov_by_key[ckey] = p
    for f in findings:
        for ev in f.get("evidence") or []:
            if not isinstance(ev, dict) or ev.get("provenance"):
                continue
            p = prov_by_key.get(canonical_key(str(ev.get("key", ""))))
            if p:
                ev["provenance"] = dict(p)
    return findings


def get_findings_from_db(version_label: str, session=None) -> list[dict]:
    """Latest persisted analysis findings for a version (read-only view)."""
    own = session is None
    session = session or new_session()
    try:
        ver = session.query(DocumentVersion).filter(
            DocumentVersion.version_label == version_label).first()
        if ver is None:
            return []
        run = (
            session.query(AnalysisRun)
            .filter(AnalysisRun.kind == "analysis",
                    AnalysisRun.version_id == ver.id)
            .order_by(AnalysisRun.id.desc())
            .first()
        )
        if run is None:
            return []
        rows = (
            session.query(Finding)
            .filter(Finding.analysis_run_id == run.id)
            .order_by(Finding.type, Finding.finding_id)
            .all()
        )
        out = [
            {
                "finding_id": r.finding_id,
                "finding_type": r.type,
                "severity": (r.severity.value if hasattr(r.severity, "value")
                             else str(r.severity)),
                "title": r.title,
                "description": r.description,
                "status": (r.status.value if hasattr(r.status, "value")
                           else str(r.status)),
                "confidence": r.confidence,
                "origin": (r.origin.value if hasattr(r.origin, "value")
                           else str(r.origin)),
                "entity_keys": list(r.related_entities_json or []),
                "fact_keys": [],
                "evidence": list(r.evidence_json or []),
                "analysis_run_id": r.analysis_run_id,
            }
            for r in rows
        ]
        return _enrich_finding_provenance(session, version_label, out)
    finally:
        if own:
            session.close()


def filter_findings(findings: list[dict], severity: str | None = None,
                    finding_type: str | None = None,
                    status: str | None = None) -> list[dict]:
    """Deterministic UI-side filter over finding dicts (M6 vocabulary)."""
    out = findings
    if severity:
        out = [f for f in out if f.get("severity") == severity]
    if finding_type:
        out = [f for f in out if f.get("finding_type") == finding_type]
    if status:
        out = [f for f in out if f.get("status") == status]
    return out


def findings_breakdown(findings: list[dict]) -> dict:
    """Severity/type/status counters for the findings screen metrics."""
    return {
        "by_severity": dict(sorted(Counter(
            f.get("severity", "?") for f in findings).items())),
        "by_type": dict(sorted(Counter(
            f.get("finding_type", "?") for f in findings).items())),
        "by_status": dict(sorted(Counter(
            f.get("status", "?") for f in findings).items())),
    }


# ---------------------------------------------------------------------------
# revision compare (M7)
# ---------------------------------------------------------------------------


def compare_revisions(base_version: str, target_version: str,
                      depth: int = 1, persist: bool = False) -> dict:
    """Run the M7 comparator and return the full comparison dict."""
    from backend.diff.comparator import RevisionComparator
    from backend.diff.persistence import persist_comparison

    comparator = RevisionComparator()
    try:
        comparison = comparator.compare(base_version, target_version,
                                        depth=depth)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    data = comparison.to_dict()
    if persist:
        session = new_session()
        try:
            persisted = persist_comparison(session, comparison, depth=depth)
            session.commit()
            data["compare_run"] = persisted
        finally:
            session.close()
    return data


# ---------------------------------------------------------------------------
# export / report (M8)
# ---------------------------------------------------------------------------


def generate_report(version: str | None = None,
                    base_version: str | None = None,
                    target_version: str | None = None,
                    include: dict | None = None,
                    session=None) -> dict:
    """Assemble the deterministic report dict (see report_service)."""
    from app.services.report_service import build_report
    return build_report(version=version, base_version=base_version,
                        target_version=target_version, include=include,
                        session=session)
