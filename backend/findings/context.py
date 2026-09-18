"""Analysis context (M6.10): one-shot load of trusted M4/M5 knowledge.

The context is constructed ONCE per analysis run from the SQLite registry
(and the M5 graph, rebuilt from the same registry) — no repeated DB scans
inside detectors (task rule 22). All lookups are pre-built maps:

- ``entities``   canonical key -> registry row (typed tables)
- ``facts``      fact_key -> ExtractionFact row
- ``provides``/``requires``  interface-key -> set of component keys
- ``carries``    interface-key -> list of signal keys (in registry order)
- ``graph``      the M5 MultiDiGraph (version-scoped)

Everything is filtered to exactly ONE DocumentVersion — version isolation
is enforced here, at the single load point, so detectors cannot cross
versions even by accident.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.graph.builder import build_graph
from backend.storage.models import (Component, Dependency, DocumentVersion,
                                    ExtractionFact, FunctionalFlow,
                                    Interface, Port, Signal)

ENTITY_TABLES: dict[str, type] = {
    "component": Component,
    "interface": Interface,
    "port": Port,
    "signal": Signal,
    "dependency": Dependency,
    "functional_flow": FunctionalFlow,
}


def canonical_key(key: str) -> str:
    """Normalize any entity key to the M4/M5 canonical form ``type:ID``.

    The type prefix is lowercase; the identifier part is uppercased to match
    the graph builder's node keys (``component:C-05``) — registry rows are
    stored uppercased by M4 persistence, so this makes context lookups and
    graph nodes agree bit-for-bit.
    """
    etype, sep, eid = key.partition(":")
    if not sep:
        return key
    return f"{etype.lower()}:{eid.strip().upper()}"


@dataclass
class AnalysisContext:
    """Trusted, version-scoped view of M4 registry + M5 graph."""

    version_id: int
    version_label: str
    document_name: str
    entities: dict[str, object]                     # key -> registry row
    facts: dict[str, object]                        # fact_key -> fact row
    provides: dict[str, set[str]]                   # ifc-key -> {comp keys}
    requires: dict[str, set[str]]                   # ifc-key -> {comp keys}
    carries: dict[str, list[str]]                   # ifc-key -> [signal keys]
    graph: object                                   # nx.MultiDiGraph
    facts_by_subject: dict[str, list[object]] = field(default_factory=dict)

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    @property
    def fact_count(self) -> int:
        return len(self.facts)


def build_analysis_context(session: Session, version_label: str) -> AnalysisContext:
    """Load everything the detectors need for exactly one version."""
    dv = session.execute(select(DocumentVersion).where(
        DocumentVersion.version_label == version_label)).scalar_one_or_none()
    if dv is None:
        raise ValueError(
            f"unknown version {version_label!r}: no DocumentVersion row "
            f"(run M4 extraction first)")
    rows = list(session.execute(select(ExtractionFact).where(
        ExtractionFact.version_id == dv.id)).scalars())

    entities: dict[str, object] = {}
    for etype, model in ENTITY_TABLES.items():
        for row in session.execute(select(model).where(
                model.version_id == dv.id)).scalars():
            entities[canonical_key(f"{etype}:{row.entity_id}")] = row

    facts: dict[str, object] = {}
    provides: dict[str, set[str]] = defaultdict(set)
    requires: dict[str, set[str]] = defaultdict(set)
    carries: dict[str, list[str]] = defaultdict(list)
    for f in rows:
        facts[f.fact_key] = f
        if f.predicate == "provides":
            provides[canonical_key(f.object)].add(canonical_key(f.subject))
        elif f.predicate == "requires":
            requires[canonical_key(f.object)].add(canonical_key(f.subject))
        elif f.predicate == "carries":
            carries[canonical_key(f.subject)].append(canonical_key(f.object))

    g, _stats = build_graph(session, version=version_label)

    return AnalysisContext(
        version_id=dv.id,
        version_label=dv.version_label,
        document_name=(dv.document.filename if dv.document else ""),
        entities=entities,
        facts=facts,
        provides=dict(provides),
        requires=dict(requires),
        carries=dict(carries),
        graph=g,
        facts_by_subject=_group_by_subject(rows),
    )


def _group_by_subject(rows: list) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for f in rows:
        out[canonical_key(f.subject)].append(f)
    return {k: v for k, v in out.items()}


def provenance_of_fact(f) -> dict:
    """Trusted provenance dict straight from ExtractionFact columns (D-022)."""
    return {
        "document_name": f.document_name,
        "version_label": f.version_label,
        "section_no": f.section_no,
        "section_title": f.section_title,
        "page_start": f.page_start,
        "page_end": f.page_end,
        "source_chunk_id": f.source_chunk_id,
    }


def provenance_of_entity(row) -> dict:
    """Trusted provenance for a typed registry row (D-022 snapshot)."""
    return {
        "document_name": "",
        "version_label": "",
        "section_no": row.section or "",
        "section_title": "",
        "page_start": row.page or 0,
        "page_end": row.page or 0,
        "source_chunk_id": "",
        "entity_id": row.entity_id,
    }
