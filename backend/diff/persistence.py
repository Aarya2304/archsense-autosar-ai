"""Comparison persistence (M7.24) — extends the existing M1 storage layer.

Reuses the existing ``CompareRun`` table (base_version_id,
target_version_id, results_json, created_at — present since M1); no new
tables. Behaviors:

- **Version-pair identity**: one row per (base, target) pair. Re-running a
  comparison for the same pair UPDATES the existing row (the comparison is
  deterministic, so same DB + same depth => identical JSON; storing the
  latest recompute is lossless) — no duplicate-comparison growth.
- **Params inside results_json**: depth and generator version are stored
  alongside the comparison payload; changing depth updates the row (the
  payload records which parameters produced it).
- **Audit**: persist/clear append audit events via the existing
  ``audit_log`` helper (M1 conventions).

The historical comparison remains reproducible from the source registry —
persistence is a convenience cache for M8, never a second source of truth.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.diff.models import RevisionComparison
from backend.storage import audit_log
from backend.storage.models import (CompareRun, DocumentVersion)

__all__ = ["persist_comparison", "load_comparison", "clear_comparison",
           "version_row_for"]


def version_row_for(session: Session, version_label: str) -> DocumentVersion:
    dv = session.execute(select(DocumentVersion).where(
        DocumentVersion.version_label == version_label)).scalar_one_or_none()
    if dv is None:
        raise ValueError(f"unknown version {version_label!r}")
    return dv


def persist_comparison(session: Session, cmp: RevisionComparison,
                       depth: int = 1) -> dict:
    """Upsert the comparison for its (base, target) pair. Idempotent."""
    base = version_row_for(session, cmp.base_version)
    target = version_row_for(session, cmp.target_version)

    row = session.execute(select(CompareRun).where(
        CompareRun.base_version_id == base.id,
        CompareRun.target_version_id == target.id)).scalar_one_or_none()
    created = row is None
    if row is None:
        row = CompareRun(base_version_id=base.id,
                         target_version_id=target.id)
        session.add(row)

    row.results_json = {
        "params": {"depth": int(depth), "generator": "m7"},
        "comparison": cmp.to_dict(),
    }
    session.commit()

    audit_log.log_event(
        session, "comparison_persisted",
        entity=f"compare:{cmp.base_version}->{cmp.target_version}",
        details={"created": created, "updated": not created,
                 "depth": int(depth),
                 "entity_changes": len(cmp.entity_changes),
                 "relationship_changes": len(cmp.relationship_changes),
                 "impacts": len(cmp.impacts),
                 "revision_findings": len(cmp.revision_findings)})
    return {"compare_run_id": row.id, "created": created,
            "updated": not created}


def load_comparison(session: Session, base_version: str,
                    target_version: str) -> dict | None:
    """Load the persisted comparison payload for a pair (None if absent)."""
    base = version_row_for(session, base_version)
    target = version_row_for(session, target_version)
    row = session.execute(select(CompareRun).where(
        CompareRun.base_version_id == base.id,
        CompareRun.target_version_id == target.id)).scalar_one_or_none()
    return row.results_json if row is not None else None


def clear_comparison(session: Session, base_version: str,
                     target_version: str) -> int:
    """Delete persisted comparisons for one pair (audited)."""
    base = version_row_for(session, base_version)
    target = version_row_for(session, target_version)
    rows = list(session.execute(select(CompareRun).where(
        CompareRun.base_version_id == base.id,
        CompareRun.target_version_id == target.id)).scalars())
    for row in rows:
        session.delete(row)
    session.commit()
    audit_log.log_event(
        session, "comparison_cleared",
        entity=f"compare:{base_version}->{target_version}",
        details={"deleted": len(rows)})
    return len(rows)
