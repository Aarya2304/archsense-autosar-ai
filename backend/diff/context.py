"""Revision context (M7): one-shot load of TWO version-scoped registries.

Built once per comparison — no repeated DB scans inside the diff/impact
code (task rule 29). Each side is a plain snapshot (canonical entity key ->
typed row, fact_key -> fact row, triple -> fact rows); version isolation is
enforced here, the single load point. The per-version graphs are built
through the M5 builder so impact traversal uses exactly the graph
semantics M5 already validated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.graph.builder import build_graph
from backend.storage.models import (Component, Dependency, DocumentVersion,
                                    ExtractionFact, FunctionalFlow, Interface,
                                    Port, Signal)

from backend.diff.models import triple_of

ENTITY_TABLES: dict[str, type] = {
    "component": Component,
    "interface": Interface,
    "port": Port,
    "signal": Signal,
    "dependency": Dependency,
    "functional_flow": FunctionalFlow,
}


def canonical_key(key: str) -> str:
    """Normalize to the M4/M5 canonical form ``type:UPPER-ID``."""
    etype, sep, eid = key.partition(":")
    if not sep:
        return key
    return f"{etype.lower()}:{eid.strip().upper()}"


@dataclass
class VersionSnapshot:
    """Trusted, version-scoped view of one M4 registry + M5 graph."""

    version_id: int
    version_label: str
    document_name: str
    entities: dict[str, object] = field(default_factory=dict)
    facts: dict[str, object] = field(default_factory=dict)
    triples: dict[tuple[str, str, str, str], list[object]] = field(
        default_factory=dict)
    graph: object = None

    @property
    def entity_keys(self) -> set[str]:
        return set(self.entities)

    @property
    def triple_set(self) -> set[tuple[str, str, str, str]]:
        return set(self.triples)


def load_version_snapshot(session: Session, version_label: str,
                          project_id: int | None = None) -> VersionSnapshot:
    """Load exactly one DocumentVersion's registry + graph (fail fast).

    When ``project_id`` is given the version must belong to that project.
    Note on the dataset model (D-036): the synthetic corpus stores each HLD
    revision as its own ``Document`` row (ABC_HLD_v1.0.0.pdf,
    ABC_HLD_v1.1.0.pdf) inside ONE project; a revision pair therefore
    shares a project, not a document row. "Unrelated documents" is thus
    enforced as "versions from different projects are rejected".
    """
    dv = session.execute(select(DocumentVersion).where(
        DocumentVersion.version_label == version_label)).scalar_one_or_none()
    if dv is None:
        raise ValueError(
            f"unknown version {version_label!r}: no DocumentVersion row "
            f"(run M4 extraction first)")
    if project_id is not None:
        doc_project = dv.document.project_id if dv.document else None
        if doc_project != project_id:
            raise ValueError(
                f"version {version_label!r} belongs to project "
                f"{doc_project}, expected project {project_id}: "
                f"cross-project (unrelated document) comparison is rejected")

    snap = VersionSnapshot(
        version_id=dv.id,
        version_label=dv.version_label,
        document_name=(dv.document.filename if dv.document else ""))

    for etype, model in ENTITY_TABLES.items():
        for row in session.execute(select(model).where(
                model.version_id == dv.id)).scalars():
            snap.entities[canonical_key(f"{etype}:{row.entity_id}")] = row

    for f in session.execute(select(ExtractionFact).where(
            ExtractionFact.version_id == dv.id)).scalars():
        snap.facts[f.fact_key] = f
        snap.triples.setdefault(triple_of(f.fact_key), []).append(f)

    g, _stats = build_graph(session, version=version_label)
    snap.graph = g
    return snap


def load_both_snapshots(session: Session, base_version: str,
                        target_version: str) -> tuple[VersionSnapshot,
                                                      VersionSnapshot]:
    """Load both sides and enforce the same-project constraint.

    base == target is rejected by the comparator; here we only require that
    both exist and belong to one project (task rule 3 — "versions from
    unrelated documents are rejected", mapped onto the project scope per
    D-036).
    """
    base = load_version_snapshot(session, base_version)
    target = load_version_snapshot(session, target_version,
                                   project_id=_project_id_of(session,
                                                             base_version))
    return base, target


def _project_id_of(session: Session, version_label: str) -> int:
    dv = session.execute(select(DocumentVersion).where(
        DocumentVersion.version_label == version_label)).scalar_one()
    return dv.document.project_id
