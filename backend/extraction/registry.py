"""SQLite registry persistence for M4 extractions (M4.10/M4.11).

Populates the M1 typed registry tables (components, interfaces, ports,
signals, dependencies, functional_flows) plus the uniform
``extraction_facts`` store. Every write goes through an
``AnalysisRun(kind="extraction")`` row and appends audit events, so any
fact can be traced: fact -> source_chunk_id -> document -> version ->
section -> page (M4.11), and re-runs upsert cleanly (deterministic keys).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.extraction.models import ExtractedEntity, ExtractedFact
from backend.storage import audit_log
from backend.storage.models import (AnalysisRun, Component, Dependency,
                                    ExtractionFact, FunctionalFlow,
                                    Interface, Port, Signal)

# registry table + (entity_id, name) extractor per entity type
_REGISTRY = {
    "component": (Component, lambda e: (e.attributes.get("id", e.name),
                                        e.attributes.get("name", e.name))),
    "interface": (Interface, lambda e: (e.attributes.get("id", e.name),
                                        e.attributes.get("name", e.name))),
    "port": (Port, lambda e: (e.attributes.get("id", e.name), e.name)),
    "signal": (Signal, lambda e: (e.attributes.get("id", e.name), e.name)),
    "dependency": (Dependency, lambda e: (e.attributes.get("id", e.name),
                                          e.name)),
    "functional_flow": (FunctionalFlow, lambda e: (e.attributes.get("id",
                                                                    e.name),
                                                   e.name)),
}


def begin_run(session: Session, version_id: int,
              params: dict | None = None) -> AnalysisRun:
    """Open an extraction run (audit row; stats filled at finish)."""
    run = AnalysisRun(version_id=version_id, kind="extraction",
                      params_json=params or {})
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
                        entity=f"version:{version_id_of(run)}",
                        details={"run_id": run.id, "stats": stats})


def version_id_of(run: AnalysisRun) -> int:
    return run.version_id


def clear_registry(session: Session, version_id: int) -> dict[str, int]:
    """Delete prior extraction output for one version (--reset path)."""
    counts: dict[str, int] = {}
    for etype, (model, _key) in _REGISTRY.items():
        res = session.execute(delete(model)
                              .where(model.version_id == version_id))
        counts[etype] = res.rowcount or 0
    res = session.execute(delete(ExtractionFact)
                          .where(ExtractionFact.version_id == version_id))
    counts["facts"] = res.rowcount or 0
    session.commit()
    audit_log.log_event(session, "extraction_registry_cleared",
                        entity=f"version:{version_id}", details=counts)
    return counts


def persist_extraction(session: Session, version_id: int,
                       entities: Sequence[ExtractedEntity],
                       facts: Sequence[ExtractedFact]) -> dict[str, int]:
    """Upsert validated entities + facts into the SQLite registry (M4.10).

    Entity identity is (version_id, entity_id); facts identity is
    (version_id, fact_key) — re-running extraction never duplicates rows.
    ``session`` is expected to have a Project/Document/DocumentVersion row
    for ``version_id`` already (created by the service when missing).
    """
    from backend.storage.models import DocumentVersion

    dv = session.get(DocumentVersion, version_id)
    doc = dv.document if dv else None
    doc_name = doc.filename if doc else ""
    version_label = dv.version_label if dv else ""

    counts = {"entities": 0, "facts": 0}

    # ---------------- typed entity tables ----------------
    for etype, (model, key_of) in _REGISTRY.items():
        sub = [e for e in entities if e.entity_type.value == etype]
        if not sub:
            continue
        # best (highest-confidence) record per canonical entity key
        best: dict[str, ExtractedEntity] = {}
        for e in sub:
            eid, _ = key_of(e)
            eid = str(eid).strip().upper() if etype != "functional_flow" \
                else str(eid).strip().upper()
            if not eid:
                continue
            prev = best.get(eid)
            if prev is None or e.confidence > prev.confidence:
                best[eid] = e
        for eid, e in best.items():
            src = e.evidence.source
            existing_pk = _existing_pk(session, model, version_id, eid)
            row = session.get(model, existing_pk) if existing_pk else None
            attrs = e.attributes
            common = dict(
                version_id=version_id,
                entity_id=eid,
                page=(src.page_start if src else 0),
                section=(src.section_no if src else ""),
                source=e.extractor,
                confidence=e.confidence,
            )
            if row is None:
                row = model(**common)
                session.add(row)
            row.page = common["page"]
            row.section = common["section"]
            row.source = e.extractor
            row.confidence = e.confidence
            if etype == "component":
                row.name = attrs.get("name", eid)
                row.type = attrs.get("type", "")
                row.layer = attrs.get("layer", "")
                row.description = attrs.get("description", "")
            elif etype == "interface":
                row.name = attrs.get("name", eid)
                row.kind = attrs.get("kind", "")
                provider = attrs.get("provider", "")
                row.provider_id = provider
            elif etype == "port":
                row.component_id = attrs.get("component_id", "")
                row.interface_id = attrs.get("interface_id", "")
                row.direction = attrs.get("direction", "")
            elif etype == "signal":
                row.name = attrs.get("name", eid)
                row.datatype = attrs.get("datatype", "")
                row.unit = attrs.get("unit", "")
                row.interface_id = attrs.get("interface_id", "")
            elif etype == "dependency":
                row.name = e.name
                row.source_id = attrs.get("source", "")
                row.target_id = attrs.get("target", "")
                row.relationship = attrs.get("relationship", "requires")
                row.evidence = attrs.get("evidence", "")
            elif etype == "functional_flow":
                row.name = attrs.get("name", e.name)
                steps = attrs.get("steps", "")
                row.steps_json = ([s.strip() for s in steps.split(">")
                                  if s.strip()]
                                 if steps else [])
                row.trigger = attrs.get("trigger", "")
                row.description = attrs.get("description", "")
            counts["entities"] += 1
    session.flush()

    # ---------------- uniform fact store ----------------
    for f in facts:
        src = f.evidence.source
        existing = session.execute(
            select(ExtractionFact).where(
                ExtractionFact.version_id == version_id,
                ExtractionFact.fact_key == f.dedupe_key)).scalar_one_or_none()
        payload = dict(
            confidence=f.confidence, extractor=f.extractor,
            document_name=(src.document_name if src else doc_name),
            version_label=(src.version if src else version_label),
            sha256=(src.sha256 if src else ""),
            section_no=(src.section_no if src else ""),
            section_title=(src.section_title if src else ""),
            page_start=(src.page_start if src else 0),
            page_end=(src.page_end if src else 0),
            source_chunk_id=(src.chunk_id if src else ""),
        )
        if existing is not None:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            session.add(ExtractionFact(
                version_id=version_id, fact_key=f.dedupe_key,
                subject=f.subject, predicate=f.predicate.value,
                object=f.object, object_value=f.object_value, **payload))
        counts["facts"] += 1
    session.commit()

    dv = session.get(DocumentVersion, version_id)
    if dv is not None:
        dv.entity_count = counts["entities"]
        session.commit()
    return counts


def _existing_pk(session: Session, model, version_id: int,
                 entity_id: str) -> int | None:
    row = session.execute(
        select(model).where(model.version_id == version_id,
                            model.entity_id == entity_id)
    ).scalar_one_or_none()
    return row.id if row else None
