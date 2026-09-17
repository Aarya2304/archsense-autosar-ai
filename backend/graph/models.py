"""Graph data model (M5): typed records derived from the M4 registry.

The graph is DERIVED knowledge — the M4 SQLite registry (typed tables +
``extraction_facts``) remains the single source of truth (D-026). Nothing
in this module invents relationships: every edge field maps 1:1 onto a
persisted ``ExtractionFact`` row, and every node field onto a typed
registry row (or the canonical key implied by a fact endpoint).

``GraphEdge`` carries a frozen ``Provenance`` snapshot taken directly from
the trusted registry columns (D-028) — the same trust model as M3
citations (D-018) and M4 extraction provenance (D-022).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Canonical node/edge attributes used on the NetworkX graph (kept here so
# builder, analysis, visualization and export all reference one vocabulary).
ATTR_KEY = "key"
ATTR_TYPE = "entity_type"
ATTR_NAME = "name"
ATTR_NORM = "normalized_name"
ATTR_VERSION = "version"
ATTR_CONFIDENCE = "confidence"
ATTR_SOURCE = "source"            # extractor that produced the entity/fact
ATTR_PROVENANCE = "provenance"    # dict (edge) — D-028 trusted snapshot
ATTR_FACT_KEY = "fact_key"        # M4 dedupe identity — edge identity
ATTR_PREDICATE = "predicate"
ATTR_OBJECT_VALUE = "object_value"


@dataclass(frozen=True)
class Provenance:
    """Trusted provenance snapshot for one edge (M4 registry columns).

    Built exclusively by the graph builder from ``ExtractionFact`` rows —
    never from LLM output (D-028).
    """

    document_name: str
    version_label: str
    section_no: str
    section_title: str
    page_start: int
    page_end: int
    source_chunk_id: str

    def to_dict(self) -> dict:
        return {
            "document_name": self.document_name,
            "version_label": self.version_label,
            "section_no": self.section_no,
            "section_title": self.section_title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "source_chunk_id": self.source_chunk_id,
            "pages_csv": (str(self.page_start)
                          if self.page_start == self.page_end
                          else f"{self.page_start}-{self.page_end}"),
        }

    def cite_line(self) -> str:
        """One-line human-readable citation (CLI / edge popups)."""
        title = f" {self.section_title}".rstrip() if self.section_title else ""
        return (f"{self.document_name} (v{self.version_label}) "
                f"section {self.section_no}{title}, "
                f"page {self.to_dict()['pages_csv']}, "
                f"chunk {self.source_chunk_id or '?'}")


@dataclass(frozen=True)
class GraphEdge:
    """One persisted M4 fact as a graph edge (identity = ``fact_key``)."""

    subject: str            # canonical node key
    predicate: str
    object: str             # canonical node key
    object_value: str
    fact_key: str           # M4 dedupe identity (subject|predicate|object|value)
    confidence: float
    extractor: str
    provenance: Provenance

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "object_value": self.object_value,
            "fact_key": self.fact_key,
            "confidence": self.confidence,
            "extractor": self.extractor,
            "provenance": self.provenance.to_dict(),
        }


@dataclass
class GraphStatistics:
    """Deterministic build/summary statistics for one version graph."""

    version: str
    node_count: int = 0
    edge_count: int = 0
    nodes_by_type: dict[str, int] = field(default_factory=dict)
    edges_by_predicate: dict[str, int] = field(default_factory=dict)
    build_time_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "nodes_by_type": dict(sorted(self.nodes_by_type.items())),
            "edges_by_predicate": dict(sorted(self.edges_by_predicate.items())),
            "build_time_ms": round(self.build_time_ms, 1),
        }


@dataclass
class GraphValidationResult:
    """Structured result of mechanical graph validation (M5.9)."""

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_nodes: int = 0
    checked_edges: int = 0

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "checked_nodes": self.checked_nodes,
            "checked_edges": self.checked_edges,
        }
