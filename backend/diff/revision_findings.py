"""Revision-level findings (M7.14, 17): rules that elevate changes to issues.

Not every change is a defect (task rule 32) — a finding is produced only
where an explicit deterministic rule establishes an architectural problem:

STALE_REFERENCE — a TARGET fact still references an entity that existed in
the BASE registry and was removed in the target revision. This is the
revision-informed form of a dangling reference: M6's UNDEFINED_REFERENCE
fires whenever a target fact references an entity absent from the *target*
registry, regardless of history; M7's STALE_REFERENCE additionally requires
the entity to have existed in base, tying the dangling reference to the
revision itself (the two coincide when the removed entity is the cause).

The rule is generic — it operates on entity keys and fact endpoints, never
on planted defect IDs. If no target fact references any removed entity,
no finding is produced (a clean removal is just a change).
"""

from __future__ import annotations

from backend.diff.context import VersionSnapshot
from backend.diff.models import (ProvenanceSnapshot, RevisionFinding,
                                 deterministic_change_id)

STALE_REFERENCE = "stale_reference"
SEVERITY_STALE_REFERENCE = "medium"   # matches the M1 severity vocabulary


def detect_revision_findings(base: VersionSnapshot,
                             target: VersionSnapshot) -> list[RevisionFinding]:
    """Deterministic revision findings for one base->target comparison."""
    out: list[RevisionFinding] = []
    removed = base.entity_keys - target.entity_keys
    if not removed:
        return out

    # target facts whose subject/object endpoint is a removed entity
    for fk in sorted(target.facts):
        f = target.facts[fk]
        affected = [k for k in (f.subject, f.object)
                    if k and k in removed]
        if not affected:
            continue
        removal_ids = [
            deterministic_change_id(base.version_label,
                                    target.version_label,
                                    "entity_removed", k)
            for k in affected]
        out.append(RevisionFinding(
            finding_id="",   # deterministic ID below
            finding_type=STALE_REFERENCE,
            base_version=base.version_label,
            target_version=target.version_label,
            severity=SEVERITY_STALE_REFERENCE,
            title=f"Stale reference to removed entity "
                  f"{'/'.join(affected)}",
            description=(
                f"Target v{target.version_label} fact {fk} still references "
                f"{', '.join(affected)}, which existed in "
                f"v{base.version_label} and was removed in this revision. "
                f"The reference must be resolved or the fact re-confirmed."),
            entity_keys=list(affected),
            fact_keys=[fk],
            change_ids=removal_ids,
            provenance=[ProvenanceSnapshot.from_fact(f)],
            metadata={"detector": "stale_reference"}))

    # deterministic content-addressed IDs + ordering
    for rf in out:
        payload = "|".join([base.version_label, target.version_label,
                            STALE_REFERENCE,
                            ",".join(sorted(rf.entity_keys)),
                            ",".join(sorted(rf.fact_keys))])
        import hashlib
        rf.finding_id = f"M7-STALEREF-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:10]}"
    out.sort(key=lambda r: r.sort_key())
    return out
