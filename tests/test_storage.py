"""Storage tests: schema, constraints, FK enforcement, audit log."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.storage.audit_log import log_event
from backend.storage.database import make_engine, make_session_factory
from backend.storage.models import (AuditEvent, Dependency, Document,
                                    DocumentVersion, Finding, Project)


@pytest.fixture()
def session():
    engine = make_engine(":memory:")
    from backend.storage.database import init_schema
    init_schema(engine)
    factory = make_session_factory(engine)
    s = factory()
    yield s
    s.close()


def _mk_doc(session, name="P"):
    p = Project(name=name)
    session.add(p)
    session.commit()
    d = Document(project_id=p.id, filename="hld.pdf", sha256="abc123")
    session.add(d)
    session.commit()
    v = DocumentVersion(document_id=d.id, version_label="1.0.0")
    session.add(v)
    session.commit()
    return p, d, v


def test_schema_creates(session):
    p, d, v = _mk_doc(session)
    assert p.id and d.id and v.id


def test_duplicate_sha_rejected(session):
    p, d, v = _mk_doc(session)
    session.add(Document(project_id=p.id, filename="other.pdf",
                         sha256="abc123"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_duplicate_version_label_rejected(session):
    p, d, v = _mk_doc(session)
    session.add(DocumentVersion(document_id=d.id, version_label="1.0.0"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_dependency_requires_version_fk(session):
    p, d, v = _mk_doc(session)
    session.add(Dependency(version_id=9999, entity_id="DEP-99",
                           source_id="C-01", target_id="C-02",
                           relationship="requires"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_audit_events_append(session):
    p, _, _ = _mk_doc(session)
    log_event(session, "document_uploaded", "documents:1", project_id=p.id,
              details={"version": "1.0.0"})
    log_event(session, "document_processed", "documents:1", project_id=p.id)
    rows = session.scalars(select(AuditEvent)).all()
    assert len(rows) == 2
    assert [r.action for r in rows] == ["document_uploaded",
                                        "document_processed"]


def test_audit_failure_does_not_raise(session):
    p, _, _ = _mk_doc(session)
    # force a failure path: details must be JSON-serializable; pass bad value
    log_event(session, "bad", details={"x": object()})
    # no exception propagated; rollback happened; table still usable
    log_event(session, "ok", "entities:none", project_id=p.id)
    rows = session.scalars(select(AuditEvent)).all()
    assert [r.action for r in rows] == ["ok"]


def test_finding_defaults(session):
    p, d, v = _mk_doc(session)
    run = __import__("backend.storage.models", fromlist=["AnalysisRun"]).AnalysisRun(
        version_id=v.id, kind="analysis")
    session.add(run)
    session.commit()
    f = Finding(analysis_run_id=run.id, finding_id="F-001", type="orphan",
                severity="medium", title="Potential orphan component",
                description="Potential inconsistency detected.",
                origin="deterministic")
    session.add(f)
    session.commit()
    assert f.status == "open"
    assert f.confidence == 0.0
