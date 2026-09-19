"""Graph builder (M5): M4 SQLite registry -> NetworkX MultiDiGraph.

Derivation-only principle (D-026): nodes come from the typed registry
tables; edges come from ``extraction_facts`` rows. No relationship is ever
invented, inferred, or LLM-supplied. Provenance on every edge is the
frozen trusted snapshot taken from the registry columns (D-028).

MultiDiGraph (D-027): the M4 fact identity (version_id, fact_key) permits
two distinct facts between the same node pair (e.g. C-x --implements-->
IF-y from two different ports). A plain DiGraph would silently collapse
them; MultiDiGraph keeps every fact as its own keyed edge
(key = fact_key), so M7's graph diff can compare fact identities 1:1.

Version isolation (D-029): ``build(version=...)`` is the default and
preferred path — nodes/edges are filtered by ``version_id`` in SQL. An
explicit all-versions mode exists but labels every node/edge with its
version and is documented as not for M7 use.
"""

from __future__ import annotations

import time

import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.graph.models import (ATTR_CONFIDENCE, ATTR_KEY, ATTR_NAME,
                                  ATTR_NORM, ATTR_OBJECT_VALUE, ATTR_PREDICATE,
                                  ATTR_PROVENANCE, ATTR_SOURCE, ATTR_TYPE,
                                  ATTR_VERSION, GraphEdge,
                                  GraphStatistics, Provenance)
from backend.storage.models import (Component, Dependency, DocumentVersion,
                                    ExtractionEntity, ExtractionFact,
                                    FunctionalFlow, Interface, Port, Signal)
from backend.graph.models import ATTR_FACT_KEY


def _node_attrs(etype: str, entity_id: str, name: str, version: str,
                confidence: float, source: str, extra: dict | None = None
                ) -> dict:
    attrs = {
        ATTR_KEY: f"{etype}:{entity_id.upper()}",
        ATTR_TYPE: etype,
        ATTR_NAME: name,
        ATTR_NORM: name.lower(),
        ATTR_VERSION: version,
        ATTR_CONFIDENCE: float(confidence),
        ATTR_SOURCE: source,
    }
    if extra:
        attrs.update({k: v for k, v in extra.items() if v})
    return attrs


def _autosar_nodes(session: Session, version_id: int,
                   version: str) -> dict[str, dict]:
    """Nodes from the M9 profile-generic ``extraction_entities`` table.

    Only profile-specific vocabularies (e.g. ``autosar:*`` keys) live here;
    ABC-profile entities are stored in the typed tables above, so the two
    sources are disjoint by construction and cannot double-register a node.
    """
    nodes: dict[str, dict] = {}
    for row in session.execute(select(ExtractionEntity).where(
            ExtractionEntity.version_id == version_id)).scalars():
        attrs = dict(row.attributes_json or {})
        prov = dict(attrs.get("provenance") or {})
        nodes[row.canonical_key] = {
            ATTR_KEY: row.canonical_key,
            ATTR_TYPE: row.entity_type,
            ATTR_NAME: row.name,
            ATTR_NORM: row.normalized_name or row.name.lower(),
            ATTR_VERSION: version,
            ATTR_CONFIDENCE: float(row.confidence),
            ATTR_SOURCE: row.source,
            "profile": row.profile,
            "page": row.page,
            "section": row.section,
            "attributes": attrs,
            # trusted provenance snapshot (task Part L): source document,
            # section, pages, chunk — identical shape to the ABC nodes'
            # provenance so UI/report code needs no special-casing.
            ATTR_PROVENANCE: {
                "document_name": prov.get("document_name", ""),
                "version_label": prov.get("version_label", version),
                "section_no": prov.get("section_no", row.section or ""),
                "section_title": prov.get("section_title", ""),
                "page_start": prov.get("page_start", row.page or 0),
                "page_end": prov.get("page_end", row.page or 0),
                "source_chunk_id": prov.get("source_chunk_id", ""),
            },
        }
    return nodes


def _typed_nodes(session: Session, version_id: int,
                 version: str) -> dict[str, dict]:
    """Canonical node attributes from the six typed registry tables.

    Rows are scalars()-fetched ORM entities (never Row tuples): a bare
    ``session.execute(select(Model))`` returns Row objects that raise
    ``AttributeError`` on ``row.entity_id`` in some SQLAlchemy paths.
    """
    nodes: dict[str, dict] = {}

    for row in session.execute(select(Component)
                               .where(Component.version_id == version_id)
                               ).scalars():
        nodes[f"component:{row.entity_id.upper()}"] = _node_attrs(
            "component", row.entity_id, row.name, version, row.confidence,
            row.source, {"description": row.description, "layer": row.layer,
                         "type": row.type, "page": row.page,
                         "section": row.section})

    for row in session.execute(select(Interface)
                               .where(Interface.version_id == version_id)
                               ).scalars():
        nodes[f"interface:{row.entity_id.upper()}"] = _node_attrs(
            "interface", row.entity_id, row.name, version, row.confidence,
            row.source, {"kind": row.kind, "page": row.page,
                         "section": row.section})

    for row in session.execute(select(Port)
                               .where(Port.version_id == version_id)
                               ).scalars():
        nodes[f"port:{row.entity_id.upper()}"] = _node_attrs(
            "port", row.entity_id, row.entity_id, version, row.confidence,
            row.source, {"component_id": row.component_id,
                         "direction": row.direction, "page": row.page,
                         "section": row.section})

    for row in session.execute(select(Signal)
                               .where(Signal.version_id == version_id)
                               ).scalars():
        nodes[f"signal:{row.entity_id.upper()}"] = _node_attrs(
            "signal", row.entity_id, row.name, version, row.confidence,
            row.source, {"datatype": row.datatype, "unit": row.unit,
                         "page": row.page, "section": row.section})

    for row in session.execute(select(Dependency)
                               .where(Dependency.version_id == version_id)
                               ).scalars():
        nodes[f"dependency:{row.entity_id.upper()}"] = _node_attrs(
            "dependency", row.entity_id, row.entity_id, version,
            row.confidence, row.source,
            {"source_id": row.source_id, "target_id": row.target_id,
             "relationship": row.relationship, "page": row.page,
             "section": row.section})

    for row in session.execute(select(FunctionalFlow)
                               .where(FunctionalFlow.version_id == version_id)
                               ).scalars():
        nodes[f"functional_flow:{row.entity_id.upper()}"] = _node_attrs(
            "functional_flow", row.entity_id, row.name, version,
            row.confidence, row.source,
            {"trigger": row.trigger, "steps": list(row.steps_json or []),
             "page": row.page, "section": row.section})

    return nodes


def resolve_version(session: Session, version: str | None) -> DocumentVersion:
    """Resolve a version label to its DocumentVersion row (fail fast)."""
    if version is None:
        raise ValueError(
            "an explicit version label is required (D-029: version-scoped "
            "graphs by default); pass version='1.0.0' or "
            "build_all_versions=True")
    dv = session.execute(select(DocumentVersion).where(
        DocumentVersion.version_label == version)).scalars().first()
    if dv is None:
        known = [v.version_label for v in
                 session.execute(select(DocumentVersion)).scalars()]
        raise ValueError(f"unknown version {version!r}; known: {known}")
    return dv


def build_graph(session: Session, version: str | None = None,
                version_id: int | None = None) -> tuple[nx.MultiDiGraph,
                                                        GraphStatistics]:
    """Build the version-scoped architecture graph from the M4 registry.

    Either ``version`` (label, preferred) or ``version_id`` must be given.
    Returns ``(MultiDiGraph, GraphStatistics)``; node keys are canonical
    M4 keys ("component:C-02"), edge keys are M4 fact_keys.
    """
    t0 = time.perf_counter()
    dv = (session.get(DocumentVersion, version_id) if version_id is not None
          else resolve_version(session, version))
    version_id = dv.id
    version_label = dv.version_label

    g = nx.MultiDiGraph()
    g.graph["version"] = version_label
    g.graph["version_id"] = version_id

    # 1. nodes from the typed registry tables (+ M9 extraction_entities)
    nodes = _typed_nodes(session, version_id, version_label)
    nodes.update(_autosar_nodes(session, version_id, version_label))
    for key, attrs in nodes.items():
        g.add_node(key, **attrs)

    # 2. edges from extraction_facts (subject/object endpoints added
    #    defensively as bare nodes so a registry inconsistency surfaces in
    #    validation rather than crashing the build)
    by_pred: dict[str, int] = {}
    for row in session.execute(select(ExtractionFact).where(
            ExtractionFact.version_id == version_id)).scalars():
        prov = Provenance(
            document_name=row.document_name,
            version_label=row.version_label or version_label,
            section_no=row.section_no,
            section_title=row.section_title,
            page_start=row.page_start,
            page_end=row.page_end,
            source_chunk_id=row.source_chunk_id,
        )
        for endpoint in (row.subject, row.object):
            if endpoint and endpoint not in g:
                etype, _, eid = endpoint.partition(":")
                g.add_node(endpoint, **_node_attrs(
                    etype, eid, eid, version_label, 0.0, "unregistered"))
        g.add_edge(row.subject, row.object, key=row.fact_key,
                   **{
                       ATTR_PREDICATE: row.predicate,
                       ATTR_OBJECT_VALUE: row.object_value,
                       ATTR_FACT_KEY: row.fact_key,
                       ATTR_CONFIDENCE: float(row.confidence),
                       ATTR_SOURCE: row.extractor,
                       ATTR_PROVENANCE: prov.to_dict(),
                   })
        by_pred[row.predicate] = by_pred.get(row.predicate, 0) + 1

    by_type: dict[str, int] = {}
    for _, attrs in g.nodes(data=True):
        et = attrs.get(ATTR_TYPE, "?")
        by_type[et] = by_type.get(et, 0) + 1

    stats = GraphStatistics(
        version=version_label,
        node_count=g.number_of_nodes(),
        edge_count=g.number_of_edges(),
        nodes_by_type=by_type,
        edges_by_predicate=by_pred,
        build_time_ms=(time.perf_counter() - t0) * 1000.0,
    )
    return g, stats


def edge_record(g: nx.MultiDiGraph, u: str, v: str, key: str) -> GraphEdge:
    """Reconstruct the typed GraphEdge for one graph edge (u, v, key)."""
    attrs = g.edges[u, v, key]
    p = attrs[ATTR_PROVENANCE]
    return GraphEdge(
        subject=u, predicate=attrs[ATTR_PREDICATE], object=v,
        object_value=attrs.get(ATTR_OBJECT_VALUE, ""),
        fact_key=attrs.get(ATTR_FACT_KEY, key),
        confidence=float(attrs.get(ATTR_CONFIDENCE, 0.0)),
        extractor=attrs.get(ATTR_SOURCE, ""),
        provenance=Provenance(
            document_name=p["document_name"],
            version_label=p["version_label"],
            section_no=p["section_no"],
            section_title=p["section_title"],
            page_start=p["page_start"],
            page_end=p["page_end"],
            source_chunk_id=p["source_chunk_id"],
        ))


def edge_records(g: nx.MultiDiGraph) -> list[GraphEdge]:
    """All edges as typed records, ordered by (subject, predicate, object)."""
    out = [edge_record(g, u, v, k) for u, v, k in g.edges(keys=True)]
    out.sort(key=lambda e: (e.subject, e.predicate, e.object, e.fact_key))
    return out
