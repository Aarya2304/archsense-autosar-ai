"""GraphService (M5.17): the typed facade over the graph subsystem.

Callers (CLI today, Streamlit in M8, M6/M7 analyses later) never touch
SQLAlchemy sessions or NetworkX internals directly:

    svc = GraphService(db_path)          # or session_factory=
    g, stats = svc.build_graph("1.0.0")
    svc.validate_graph(g)
    svc.related("C-02")                  # friendly-id resolution built in
    svc.render_html(g, path)

The M4 registry remains the source of truth (D-026); every call rebuilds
or filters deterministically from it.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
from sqlalchemy.orm import Session, sessionmaker

from backend.graph import analysis, export, filtering, validation
from backend.graph.builder import build_graph, edge_records
from backend.graph.models import (ATTR_PROVENANCE, GraphEdge,
                                  GraphStatistics, GraphValidationResult)
from backend.storage.database import (make_engine, make_session_factory)


class GraphService:
    """High-level, UI-independent graph API (M5.17)."""

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

    # ------------------------------------------------------------- build --
    def build_graph(self, version: str) -> tuple[nx.MultiDiGraph,
                                                 GraphStatistics]:
        session = self._session()
        try:
            return build_graph(session, version=version)
        finally:
            session.close()

    # --------------------------------------------------------- validate --
    def validate_graph(self, g: nx.MultiDiGraph) -> GraphValidationResult:
        session = self._session()
        try:
            return validation.validate_graph(g, session)
        finally:
            session.close()

    # ----------------------------------------------------------- filter --
    def filter_graph(self, g: nx.MultiDiGraph, **kwargs) -> nx.MultiDiGraph:
        return filtering.filter_graph(g, **kwargs)

    # ---------------------------------------------------------- queries --
    def related(self, identifier: str, version: str,
                graph: nx.MultiDiGraph | None = None) -> dict:
        g = graph if graph is not None else self.build_graph(version)[0]
        key = analysis.resolve_key(g, identifier)
        if key is None:
            raise ValueError(f"cannot resolve {identifier!r} in v{version}")
        return analysis.related(g, key)

    def shortest_path(self, source: str, target: str, version: str,
                      directed: bool = True,
                      graph: nx.MultiDiGraph | None = None
                      ) -> list[str] | None:
        g = graph if graph is not None else self.build_graph(version)[0]
        s = analysis.resolve_key(g, source)
        t = analysis.resolve_key(g, target)
        if s is None or t is None:
            return None
        return analysis.shortest_path(g, s, t, directed=directed)

    def statistics(self, version: str) -> dict:
        _, stats = self.build_graph(version)
        return stats.to_dict()

    # ----------------------------------------------------------- export --
    def export_json(self, g: nx.MultiDiGraph, path: Path,
                    stats: GraphStatistics | None = None) -> Path:
        return export.export_json(g, path, stats)

    def export_graphml(self, g: nx.MultiDiGraph, path: Path) -> Path:
        return export.export_graphml(g, path)

    def render_html(self, g: nx.MultiDiGraph, path: Path,
                    **kwargs) -> Path:
        from backend.graph.visualization import render_html
        return render_html(g, path, **kwargs)

    # ---------------------------------------------------------- helpers --
    @staticmethod
    def edge_records(g: nx.MultiDiGraph) -> list[GraphEdge]:
        return edge_records(g)

    @staticmethod
    def edge_provenance(g: nx.MultiDiGraph, u: str, v: str, key: str) -> dict:
        """Trusted provenance dict for one edge (u, v, key)."""
        return dict(g.edges[u, v, key].get(ATTR_PROVENANCE, {}))
