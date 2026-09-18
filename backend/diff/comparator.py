"""RevisionComparator (M7.21): the typed facade over the diff subsystem.

Callers (CLI today, M8 UI later) never touch snapshots or NetworkX:

    cmp = RevisionComparator(db_path).compare("1.0.0", "1.1.0")
    cmp.summary.entity_removed
    cmp.to_json()

The comparison is deterministic: same DB + same version pair + same depth
=> identical output (sorting rules in models, traversal order in impact).
"""

from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from backend.diff import (entity_diff, impact, relationship_diff,
                          revision_findings, validation)
from backend.diff.context import load_both_snapshots
from backend.diff.models import RevisionComparison, RevisionSummary
from backend.storage.database import (make_engine, make_session_factory)
from backend.storage.models import DocumentVersion
from sqlalchemy import select


class RevisionComparator:
    """High-level, UI-independent revision-comparison API (M7.21)."""

    def __init__(self, db_path: Path | None = None,
                 session_factory: sessionmaker | None = None) -> None:
        if session_factory is not None:
            self._factory = session_factory
        else:
            from backend.config import DB_PATH
            engine = make_engine(db_path or DB_PATH)
            self._factory = make_session_factory(engine)

    def _session(self) -> Session:
        return self._factory()

    # ------------------------------------------------------------ compare --
    def compare(self, base_version: str, target_version: str,
                depth: int = 1) -> RevisionComparison:
        if not base_version or not target_version:
            raise ValueError("both --base-version and --target-version are "
                             "required")
        if str(base_version) == str(target_version):
            raise ValueError(
                f"base and target versions are identical "
                f"({base_version!r}): a comparison requires two distinct "
                f"versions")
        if int(depth) < 0:
            raise ValueError("depth must be >= 0")

        t0 = time.perf_counter()
        session = self._session()
        try:
            base, target = load_both_snapshots(session, str(base_version),
                                               str(target_version))
            t_ctx = time.perf_counter()

            ents = entity_diff.diff_entities(base, target)
            rels = relationship_diff.diff_relationships(base, target)
            t_diff = time.perf_counter()

            imps = impact.analyze_impacts(ents, rels, base, target,
                                          max_depth=int(depth))
            t_imp = time.perf_counter()

            rev_findings = revision_findings.detect_revision_findings(
                base, target)
            t_find = time.perf_counter()

            cmp = RevisionComparison(
                base_version=base.version_label,
                target_version=target.version_label,
                document_name=(base.document_name
                               or target.document_name),
                entity_changes=ents,
                relationship_changes=rels,
                impacts=imps,
                revision_findings=rev_findings,
                summary=RevisionSummary(
                    entity_added=sum(1 for c in ents
                                     if c.change_type.value
                                     == "entity_added"),
                    entity_removed=sum(1 for c in ents
                                       if c.change_type.value
                                       == "entity_removed"),
                    entity_changed=sum(1 for c in ents
                                       if c.change_type.value
                                       == "entity_changed"),
                    relationship_added=sum(
                        1 for c in rels
                        if c.change_type.value == "relationship_added"),
                    relationship_removed=sum(
                        1 for c in rels
                        if c.change_type.value
                        == "relationship_removed"),
                    impact_count=len(imps),
                    impacts_by_category=_count(imps,
                                               lambda i: i.category.value),
                    relationship_changes_by_predicate=_count(
                        rels, lambda c: c.predicate),
                    entity_changes_by_type=_count(
                        ents, lambda c: c.entity_type),
                    revision_finding_count=len(rev_findings)),
            )
            cmp.validation = validation.validate_comparison(
                cmp,
                graphs={base.version_label: base.graph,
                        target.version_label: target.graph},
                max_depth=int(depth)).to_dict()
            t_val = time.perf_counter()

            cmp.timings_ms = {
                "context_ms": round((t_ctx - t0) * 1000.0, 1),
                "entity_diff_ms": round((t_diff - t_ctx) * 1000.0, 1),
                "impact_ms": round((t_imp - t_diff) * 1000.0, 1),
                "findings_ms": round((t_find - t_imp) * 1000.0, 1),
                "validation_ms": round((t_val - t_find) * 1000.0, 1),
                "total_ms": round((t_val - t0) * 1000.0, 1),
            }
            return cmp
        finally:
            session.close()


def _count(items, fn) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        k = fn(it)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def known_versions(session: Session) -> list[str]:
    """All version labels, for CLI error messages."""
    return [dv.version_label
            for dv in session.execute(select(DocumentVersion)).scalars()]
