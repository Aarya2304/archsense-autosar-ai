"""M4 tests: validation rules + SQLite registry + end-to-end pipeline.

Covers: evidence resolution (unknown IDs, missing evidence), confidence
floor, duplicate dedupe, provenance propagation, registry persistence
(typed tables + extraction_facts), idempotent re-runs, reset, audit trail,
and the full document -> chunks -> extraction -> SQLite -> query path on
an isolated per-test database.
"""

from __future__ import annotations

import pytest

from backend.extraction.models import (EntityType, EvidenceRef,
                                       ExtractedEntity, ExtractedFact,
                                       Predicate, Source)
from backend.extraction.service import ExtractionService
from backend.extraction.validator import validate_extraction
from backend.rag.chunker import chunk_processed_json


def _src(chunk_id="c1", page=5) -> Source:
    return Source(document_name="d.pdf", version="1.0.0", page_start=page,
                  page_end=page, chunk_id=chunk_id)


def _emap() -> dict:
    return {"E1": _src("c1", 5), "E2": _src("c2", 7)}


# ------------------------------------------------------------- validation ----

def test_validate_resolves_evidence_ids():
    e = ExtractedEntity(entity_type=EntityType.COMPONENT, name="C-08",
                        attributes={"id": "C-08"},
                        evidence=EvidenceRef(evidence_id="E1"),
                        confidence=0.9)
    out = validate_extraction([e], _emap())
    assert out.ok and len(out.entities) == 1
    assert out.entities[0].evidence.source is None   # ID kept, not yet resolved


def test_validate_rejects_unknown_evidence_id():
    e = ExtractedEntity(entity_type=EntityType.COMPONENT, name="C-08",
                        attributes={"id": "C-08"},
                        evidence=EvidenceRef(evidence_id="E999"),
                        confidence=0.9)
    out = validate_extraction([e], _emap())
    assert not out.ok
    assert out.entities == []
    assert any(i.kind == "unknown_evidence_id" for i in out.issues)
    assert out.stats.unknown_evidence_ids == 1


def test_validate_missing_evidence_rejected():
    e = ExtractedEntity(entity_type=EntityType.COMPONENT, name="C-08",
                        evidence=EvidenceRef(evidence_id=""), confidence=0.9)
    out = validate_extraction([e], _emap())
    assert not out.ok
    assert any(i.kind == "missing_evidence" for i in out.issues)


def test_validate_confidence_floor():
    e = ExtractedEntity(entity_type=EntityType.COMPONENT, name="C-08",
                        attributes={"id": "C-08"},
                        evidence=EvidenceRef(evidence_id="E1"),
                        confidence=0.2)
    out = validate_extraction([e], _emap(), min_confidence=0.5)
    assert out.entities == []
    assert any(i.kind == "below_min_confidence" for i in out.issues)


def test_validate_dedupes_duplicate_facts():
    f1 = ExtractedFact(subject="component:C-02", predicate=Predicate.PROVIDES,
                       object="interface:IF-03",
                       evidence=EvidenceRef(evidence_id="E1"), confidence=0.9)
    f2 = ExtractedFact(subject="component:C-02", predicate=Predicate.PROVIDES,
                       object="interface:IF-03",
                       evidence=EvidenceRef(evidence_id="E2"), confidence=0.8)
    ents = [
        ExtractedEntity(entity_type=EntityType.COMPONENT, name="C-02",
                        attributes={"id": "C-02"},
                        evidence=EvidenceRef(evidence_id="E1"),
                        confidence=0.9),
        ExtractedEntity(entity_type=EntityType.INTERFACE, name="IF-03",
                        attributes={"id": "IF-03"},
                        evidence=EvidenceRef(evidence_id="E1"),
                        confidence=0.9),
    ]
    out = validate_extraction([*ents, f1, f2], _emap())
    assert out.ok
    assert len(out.facts) == 1
    assert out.stats.duplicate_facts == 1
    assert out.facts[0].confidence == 0.9     # best confidence wins


def test_validate_unresolvable_reference_rejected():
    f = ExtractedFact(subject="component:C-42", predicate=Predicate.PROVIDES,
                      object="interface:IF-03",
                      evidence=EvidenceRef(evidence_id="E1"), confidence=0.9)
    ents = [ExtractedEntity(entity_type=EntityType.INTERFACE, name="IF-03",
                            attributes={"id": "IF-03"},
                            evidence=EvidenceRef(evidence_id="E1"),
                            confidence=0.9)]
    out = validate_extraction([*ents, f], _emap())
    assert not out.ok
    assert any(i.kind == "unresolvable_reference" for i in out.issues)


def test_validate_alias_canonicalization_merges_name_forms():
    """Facts referencing 'DoorStatusIF' by name collapse to IF-01 when the
    alias map says so (C-02-style name-key -> ID-key canonicalization)."""
    aliases = {"interface:doorstatusif": "interface:IF-01"}
    ents = [
        ExtractedEntity(entity_type=EntityType.COMPONENT, name="C-08",
                        attributes={"id": "C-08"},
                        evidence=EvidenceRef(evidence_id="E1"),
                        confidence=0.9),
        ExtractedEntity(entity_type=EntityType.INTERFACE, name="IF-01",
                        attributes={"id": "IF-01"},
                        evidence=EvidenceRef(evidence_id="E1"),
                        confidence=0.9),
    ]
    f = ExtractedFact(subject="component:C-08", predicate=Predicate.REQUIRES,
                      object="interface:doorstatusif",
                      evidence=EvidenceRef(evidence_id="E1"), confidence=0.9)
    out = validate_extraction([*ents, f], _emap(), aliases=aliases)
    assert out.ok
    assert out.facts[0].object == "interface:IF-01"


# ------------------------------------------------- persistence (isolated DB) --

@pytest.fixture()
def tmp_db(tmp_path):
    return tmp_path / "registry.db"


def _extract(chunks):
    svc = ExtractionService()
    return svc.extract_from_chunks(chunks, document_name="t.pdf",
                                   version="1.0.0")


def test_persist_and_query_roundtrip(tmp_db, tmp_path):
    from sqlalchemy import func, select
    from backend.storage.database import init_schema, make_engine, make_session_factory
    from backend.storage.models import (Component, Dependency, ExtractionFact,
                                        FunctionalFlow, Interface, Port,
                                        Signal)
    chunks = chunk_processed_json(
        "data/processed/ABC_HLD_v1.0.0__processed.json")
    svc = ExtractionService(db_path=tmp_db)
    res = svc.extract_from_chunks(chunks, document_name="d.pdf",
                                  version="1.0.0", persist=True)
    assert res.run_id is not None

    engine = make_engine(tmp_db)
    init_schema(engine)
    session = make_session_factory(engine)()
    assert session.execute(select(func.count())
                           .select_from(Component)).scalar() == 20
    assert session.execute(select(func.count())
                           .select_from(Interface)).scalar() == 25
    assert session.execute(select(func.count())
                           .select_from(Port)).scalar() == 57
    assert session.execute(select(func.count())
                           .select_from(Signal)).scalar() == 34
    assert session.execute(select(func.count())
                           .select_from(Dependency)).scalar() == 24
    assert session.execute(select(func.count())
                           .select_from(FunctionalFlow)).scalar() == 5
    assert session.execute(select(func.count())
                           .select_from(ExtractionFact)).scalar() == 200

    # every fact row carries trusted provenance (from the CHUNK, not the
    # caller's display label — D-022: provenance is never caller-supplied)
    row = session.execute(select(ExtractionFact).where(
        ExtractionFact.subject == "component:C-10",
        ExtractionFact.predicate == "provides",
        ExtractionFact.object == "interface:IF-13")).scalar_one()
    assert row.document_name == "ABC_HLD_v1.0.0.pdf"
    assert row.version_label == "1.0.0"
    assert row.page_start >= 1
    assert row.source_chunk_id
    assert row.section_no
    session.close()


def test_persist_idempotent_no_duplicates(tmp_db):
    """Re-running extraction must not duplicate registry rows."""
    from sqlalchemy import func, select
    from backend.storage.database import init_schema, make_engine, make_session_factory
    from backend.storage.models import ExtractionFact, Port
    chunks = chunk_processed_json(
        "data/processed/ABC_HLD_v1.0.0__processed.json")
    svc = ExtractionService(db_path=tmp_db)
    for _ in range(2):
        svc.extract_from_chunks(chunks, document_name="d.pdf",
                                version="1.0.0", persist=True)
    engine = make_engine(tmp_db)
    init_schema(engine)
    session = make_session_factory(engine)()
    assert session.execute(select(func.count())
                           .select_from(ExtractionFact)).scalar() == 200
    assert session.execute(select(func.count())
                           .select_from(Port)).scalar() == 57
    session.close()


def test_reset_clears_registry(tmp_db):
    from sqlalchemy import func, select
    from backend.storage.database import init_schema, make_engine, make_session_factory
    from backend.storage.models import ExtractionFact
    chunks = chunk_processed_json(
        "data/processed/ABC_HLD_v1.0.0__processed.json")
    svc = ExtractionService(db_path=tmp_db)
    svc.extract_from_chunks(chunks, document_name="d.pdf", version="1.0.0",
                            persist=True)
    svc.extract_from_chunks(chunks, document_name="d.pdf", version="1.0.0",
                            persist=True, reset=True)
    engine = make_engine(tmp_db)
    init_schema(engine)
    session = make_session_factory(engine)()
    assert session.execute(select(func.count())
                           .select_from(ExtractionFact)).scalar() == 200
    session.close()


def test_audit_trail_records_extraction(tmp_db):
    from sqlalchemy import select
    from backend.storage.database import init_schema, make_engine, make_session_factory
    from backend.storage.models import AuditEvent
    chunks = chunk_processed_json(
        "data/processed/ABC_HLD_v1.0.0__processed.json")
    svc = ExtractionService(db_path=tmp_db)
    svc.extract_from_chunks(chunks, document_name="d.pdf", version="1.0.0",
                            persist=True)
    engine = make_engine(tmp_db)
    init_schema(engine)
    session = make_session_factory(engine)()
    actions = {a.action for a in
               session.execute(select(AuditEvent)).scalars()}
    assert "extraction_run_started" in actions
    assert "extraction_run_finished" in actions
    session.close()


# --------------------------------------------------------------- end-to-end --

def test_end_to_end_provenance_chain(tmp_db):
    """fact -> chunk -> document -> version -> section -> page (M4.11)."""
    chunks = chunk_processed_json(
        "data/processed/ABC_HLD_v1.0.0__processed.json")
    svc = ExtractionService(db_path=tmp_db)
    res = svc.extract_from_chunks(chunks, document_name="d.pdf",
                                  version="1.0.0", persist=True)
    fact = next(f for f in res.facts
                if (f.subject, f.predicate.value, f.object)
                == ("component:C-10", "provides", "interface:IF-13"))
    src = fact.evidence.source
    assert src.chunk_id in {c.chunk_id for c in chunks}
    assert src.document_name == "ABC_HLD_v1.0.0.pdf"   # trusted chunk name
    assert src.version == "1.0.0"
    assert src.section_no
    assert 1 <= src.page_start <= src.page_end


def test_end_to_end_summary_string(tmp_db):
    chunks = chunk_processed_json(
        "data/processed/ABC_HLD_v1.0.0__processed.json")
    svc = ExtractionService(db_path=tmp_db)
    res = svc.extract_from_chunks(chunks, document_name="d.pdf",
                                  version="1.0.0", persist=True)
    s = res.summary()
    assert "component=20" in s and "interface=25" in s
    # canonical (post-dedupe) fact counts == gold: 25 provides, 34 carries
    assert "provides=25" in s and "carries=34" in s
    assert "Run" in s
