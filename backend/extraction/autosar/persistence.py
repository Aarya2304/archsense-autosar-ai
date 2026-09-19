"""AUTOSAR registry persistence (M9): extraction_entities + extraction_facts.

Reuses the M4 audit-run conventions (``AnalysisRun(kind="extraction")`` +
audit events) and the uniform ``extraction_facts`` store; entities go to the
new profile-generic ``extraction_entities`` table (the six typed ABC tables
remain untouched — profile separation, D-048). Re-running upserts
deterministically: entity identity is (version_id, canonical_key), fact
identity is (version_id, fact_key).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.extraction.autosar.models import AutosarEntity, AutosarFact
from backend.extraction.models import Source
from backend.storage import audit_log
from backend.storage.models import (AnalysisRun, DocumentVersion,
                                    ExtractionEntity, ExtractionFact,
                                    EntityStatus)


def begin_run(session: Session, version_id: int,
              params: dict | None = None) -> AnalysisRun:
    run = AnalysisRun(version_id=version_id, kind="extraction",
                      params_json={"profile": "autosar_adaptive_platform",
                                   **(params or {})})
    session.add(run)
    session.commit()
    audit_log.log_event(session, "extraction_run_started",
                        entity=f"version:{version_id}",
                        details={"run_id": run.id, "params": params or {}})
    return run


def finish_run(session: Session, run: AnalysisRun, stats: dict) -> None:
    run.stats_json = stats
    session.commit()
    audit_log.log_event(session, "extraction_run_finished",
                        entity=f"version:{run.version_id}",
                        details={"run_id": run.id, "stats": stats})


def clear_registry(session: Session, version_id: int) -> dict[str, int]:
    """Delete prior AUTOSAR extraction output for one version (audited)."""
    n_ent = session.execute(delete(ExtractionEntity).where(
        ExtractionEntity.version_id == version_id)).rowcount or 0
    n_fact = session.execute(delete(ExtractionFact).where(
        ExtractionFact.version_id == version_id,
        ExtractionFact.subject.like("autosar:%"))).rowcount or 0
    session.commit()
    counts = {"entities": n_ent, "facts": n_fact}
    audit_log.log_event(session, "extraction_registry_cleared",
                        entity=f"version:{version_id}", details=counts)
    return counts


def persist_extraction(session: Session, version_id: int,
                       entities: Sequence[AutosarEntity],
                       facts: Sequence[AutosarFact],
                       profile: str = "autosar_adaptive_platform"
                       ) -> dict[str, int]:
    """Upsert validated AUTOSAR entities + facts (idempotent)."""
    dv = session.get(DocumentVersion, version_id)
    doc = dv.document if dv else None
    doc_name = doc.filename if doc else ""
    version_label = dv.version_label if dv else ""

    counts = {"entities": 0, "facts": 0}

    for e in entities:
        src = e.evidence.source
        existing = session.execute(
            select(ExtractionEntity).where(
                ExtractionEntity.version_id == version_id,
                ExtractionEntity.canonical_key == e.key)
        ).scalar_one_or_none()
        # full trusted snapshot in attributes_json so the M5 graph builder
        # can expose document/version/pages/chunk on the node (task Part L)
        attrs = dict(e.attributes or {})
        if src is not None:
            attrs["provenance"] = {
                "document_name": src.document_name,
                "version_label": src.version,
                "section_no": src.section_no,
                "section_title": src.section_title,
                "page_start": src.page_start,
                "page_end": src.page_end,
                "source_chunk_id": src.chunk_id,
                "chunk_id": src.chunk_id,   # AutosarSource.to_dict() key
            }
        payload = dict(
            entity_type=e.entity_type.value,
            name=e.name,
            normalized_name=e.normalized_name,
            attributes_json=attrs,
            profile=profile,
            page=(src.page_start if src else 0),
            section=(src.section_no if src else ""),
            source=e.extractor,
            confidence=e.confidence,
        )
        if existing is None:
            existing = ExtractionEntity(
                version_id=version_id, canonical_key=e.key, **payload)
            session.add(existing)
        else:
            for k, v in payload.items():
                setattr(existing, k, v)
        counts["entities"] += 1
    session.flush()

    for f in facts:
        src = f.evidence.source
        fact_key = f.dedupe_key
        existing = session.execute(
            select(ExtractionFact).where(
                ExtractionFact.version_id == version_id,
                ExtractionFact.fact_key == fact_key)
        ).scalar_one_or_none()
        payload = dict(
            confidence=f.confidence,
            extractor=f.extractor,
            document_name=(src.document_name if src else doc_name),
            version_label=(src.version if src else version_label),
            sha256=(src.sha256 if src else ""),
            section_no=(src.section_no if src else ""),
            section_title=(src.section_title if src else ""),
            page_start=(src.page_start if src else 0),
            page_end=(src.page_end if src else 0),
            source_chunk_id=(src.chunk_id if src else ""),
        )
        if existing is None:
            session.add(ExtractionFact(
                version_id=version_id, fact_key=fact_key,
                subject=f.subject, predicate=f.predicate.value,
                object=f.object, object_value="", **payload))
        else:
            for k, v in payload.items():
                setattr(existing, k, v)
        counts["facts"] += 1

    session.commit()

    dv = session.get(DocumentVersion, version_id)
    if dv is not None:
        dv.entity_count = session.execute(
            select(__import__("sqlalchemy").func.count())
            .select_from(ExtractionEntity)
            .where(ExtractionEntity.version_id == version_id)
        ).scalar() or 0
        session.commit()
    return counts


def source_of_entity(row: ExtractionEntity) -> dict:
    """Trusted provenance dict for a persisted AUTOSAR entity row."""
    return {
        "document_name": "",
        "version_label": "",
        "section_no": row.section or "",
        "section_title": "",
        "page_start": row.page or 0,
        "page_end": row.page or 0,
        "source_chunk_id": "",
        "entity_id": row.canonical_key,
    }


__all__ = ["begin_run", "finish_run", "clear_registry", "persist_extraction",
           "source_of_entity", "EntityStatus"]
