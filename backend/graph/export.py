"""Graph export (M5.16): deterministic JSON serialization.

node-link format keeps every node/edge attribute verbatim (including the
trusted provenance dicts), wrapped with version + statistics. GraphML is
offered as a secondary format; note that GraphML cannot hold nested dicts,
so provenance is flattened to a CSV citation line there.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import networkx as nx

from backend.graph.builder import edge_records
from backend.graph.models import ATTR_TYPE, GraphStatistics


def _stats_from_graph(g: nx.MultiDiGraph, version: str) -> GraphStatistics:
    """Derive statistics from the graph itself (export without a prior
    build call)."""
    from collections import Counter
    nt = Counter(d.get(ATTR_TYPE, "?") for _, d in g.nodes(data=True))
    ep = Counter(d.get("predicate", "?") for _, _, d in g.edges(data=True))
    return GraphStatistics(
        version=version, node_count=g.number_of_nodes(),
        edge_count=g.number_of_edges(),
        nodes_by_type=dict(sorted(nt.items())),
        edges_by_predicate=dict(sorted(ep.items())))


def to_json_dict(g: nx.MultiDiGraph, stats: GraphStatistics | None = None
                 ) -> dict:
    version = g.graph.get("version", "")
    if stats is None:
        stats = _stats_from_graph(g, version)
    data = nx.node_link_data(g, edges="edges")
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": version,
        "statistics": stats.to_dict(),
        **data,
    }
    return payload


def export_json(g: nx.MultiDiGraph, path: Path,
                stats: GraphStatistics | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_json_dict(g, stats), indent=2),
                    encoding="utf-8")
    return path


def export_graphml(g: nx.MultiDiGraph, path: Path) -> Path:
    """GraphML export with flattened provenance (lossy by necessity)."""
    flat = nx.MultiDiGraph()
    flat.graph.update({k: str(v) for k, v in g.graph.items()})
    for n, d in g.nodes(data=True):
        attrs = {k: (v if isinstance(v, (int, float, bool)) or v is None
                     else json.dumps(v) if isinstance(v, (list, dict))
                     else str(v)) for k, v in d.items()}
        flat.add_node(n, **attrs)
    for u, v, k, d in g.edges(keys=True, data=True):
        prov = d.get("provenance", {})
        attrs = {kk: (vv if isinstance(vv, (int, float, bool)) or vv is None
                      else json.dumps(vv) if isinstance(vv, (list, dict))
                      else str(vv))
                 for kk, vv in d.items() if kk != "provenance"}
        prov_flat = "; ".join(
            f"{kk}={prov[kk]}" for kk in
            ("document_name", "version_label", "section_no", "section_title",
             "page_start", "page_end", "source_chunk_id") if kk in prov)
        attrs["provenance_flat"] = prov_flat
        flat.add_edge(u, v, key=k, **attrs)
    path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(flat, path)
    return path


def edges_payload(g: nx.MultiDiGraph) -> list[dict]:
    """Typed edge records (provenance included) as JSON-able dicts."""
    return [e.to_dict() for e in edge_records(g)]
