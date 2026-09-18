"""Findings persistence (M6.12) — extends the existing M1 storage layer.

Reuses the existing ``AnalysisRun(kind="analysis")`` + ``Finding`` tables;
no new tables. Key behaviors:

- **Idempotent re-runs**: finding identity is the deterministic
  ``finding_id`` within the latest run for a version. A fresh run upserts:
  same finding_id -> update in place (review status/comment preserved);
  findings no longer produced by the current code are removed from the new
  run's record set (a run reflects what the CURRENT detectors see).
- **Review preservation**: when a finding re-appears, its ``status``,
  ``reviewer_comment`` and ``reviewed_at`` are carried over from the previous
  run so human review work is never lost (existing M1 workflow fields).
- **Audit**: run start/finish and reset all append audit events via the
  existing ``audit_log`` helper (M1 conventions).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.findings.models import Finding
from backend.storage import audit_log
from backend.storage.models import (AnalysisRun, DocumentVersion, Finding as
                                    FindingRow, FindingSeverity, FindingStatus,
                                    FindingOrigin)

__all__ = ["begin_analysis_run", "finish_analysis_run", "persist_findings",
           "clear_findings", "latest_run", "load_findings"]


def latest_run(session: Session, version_id: int) -> AnalysisRun | None:
    return session.execute(
        select(AnalysisRun)
        .where(AnalysisRun.version_id == version_id,
               AnalysisRun.kind == "analysis")
        .order_by(AnalysisRun.id.desc())).scalars().first()


def begin_analysis_run(session: Session, version_id: int,
                       params: dict | None = None) -> AnalysisRun:
    run = AnalysisRun(version_id=version_id, kind="analysis",
                      params_json=params or {})
    session.add(run)
    session.commit()
    audit_log.log_event(session, "findings_run_started",
                        entity=f"version:{version_id}",
                        details={"run_id": run.id, "params": params or {}})
    return run


def finish_analysis_run(session: Session, run: AnalysisRun,
                        stats: dict) -> None:
    run.stats_json = stats
    run.finished_at = __import__("datetime").datetime.utcnow()
    session.commit()
    audit_log.log_event(session, "findings_run_finished",
                        entity=f"version:{run.version_id}",
                        details={"run_id": run.id, "stats": stats})


def clear_findings(session: Session, version_id: int) -> int:
    """--reset path: delete all analysis findings for one version (audited)."""
    n = 0
    for run in session.execute(
            select(AnalysisRun).where(
                AnalysisRun.version_id == version_id,
                AnalysisRun.kind == "analysis")).scalars():
        for row in list(run.findings):
            session.delete(row)
            n += 1
        session.delete(run)
    session.commit()
    audit_log.log_event(session, "findings_cleared",
                        entity=f"version:{version_id}", details={"deleted": n})
    return n


def persist_findings(session: Session, version_id: int,
                     findings: list[Finding],
                     params: dict | None = None) -> dict:
    """Upsert one analysis run's findings (idempotent, review-preserving).

    Returns stats: {"run_id", "persisted", "updated", "removed"}.
    """
    # carry-over map from the previous run (by deterministic finding_id)
    prev = latest_run(session, version_id)
    prev_by_id: dict[str, FindingRow] = {}
    if prev is not None:
        prev_by_id = {row.finding_id: row for row in prev.findings}

    run = AnalysisRun(version_id=version_id, kind="analysis",
                      params_json=params or {})
    session.add(run)
    session.flush()                       # assign run.id

    persisted = updated = 0
    seen_ids: set[str] = set()
    for f in findings:
        seen_ids.add(f.finding_id)
        existing = prev_by_id.get(f.finding_id)
        row = FindingRow(
            analysis_run_id=run.id,
            finding_id=f.finding_id,
            type=f.finding_type.value if hasattr(f.finding_type, "value")
            else str(f.finding_type),
            severity=FindingSeverity(
                f.severity.value if hasattr(f.severity, "value")
                else str(f.severity)),
            title=f.title[:255],
            description=f.description,
            evidence_json=[e.model_dump(mode="json") for e in f.evidence],
            related_entities_json=list(f.entity_keys),
            origin=FindingOrigin.DETERMINISTIC,
            confidence=f.confidence,
            status=FindingStatus.OPEN,
        )
        if existing is not None:
            # preserve human review state across runs
            row.status = existing.status
            row.reviewer_comment = existing.reviewer_comment
            row.reviewed_at = existing.reviewed_at
            updated += 1
        session.add(row)
        persisted += 1

    session.commit()
    removed = 0
    if prev is not None:
        gone = [row for fid, row in prev_by_id.items() if fid not in seen_ids]
        for row in gone:
            session.delete(row)
        removed = len(gone)
        session.commit()

    audit_log.log_event(session, "findings_persisted",
                        entity=f"version:{version_id}",
                        details={"run_id": run.id, "persisted": persisted,
                                 "carried_review_state": updated,
                                 "removed": removed})
    return {"run_id": run.id, "persisted": persisted, "updated": updated,
            "removed": removed}


def load_findings(session: Session, version_id: int,
                  run_id: int | None = None) -> list[FindingRow]:
    """Load finding rows for a version (latest run unless run_id given)."""
    run = (session.get(AnalysisRun, run_id) if run_id is not None
           else latest_run(session, version_id))
    if run is None:
        return []
    return sorted(run.findings, key=lambda r: (r.type, r.finding_id))


def version_id_for(session: Session, version_label: str) -> int:
    dv = session.execute(select(DocumentVersion).where(
        DocumentVersion.version_label == version_label)).scalar_one_or_none()
    if dv is None:
        raise ValueError(f"unknown version {version_label!r}")
    return dv.id
