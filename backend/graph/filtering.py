"""Graph filtering (M5.12): deterministic subgraph selection.

Filters never mutate the source registry or the source graph — they return
a new induced subgraph (all node/edge attributes preserved verbatim, so
provenance survives filtering).
"""

from __future__ import annotations

import networkx as nx

from backend.graph.models import (ATTR_CONFIDENCE, ATTR_TYPE,
                                  ATTR_PREDICATE)


def filter_graph(g: nx.MultiDiGraph,
                 entity_type: str | None = None,
                 predicate: str | None = None,
                 min_confidence: float | None = None,
                 node: str | None = None,
                 depth: int | None = None,
                 version: str | None = None) -> nx.MultiDiGraph:
    """Return an induced subgraph matching ALL supplied filters.

    - ``entity_type``: keep nodes of this type (and edges between them);
      when combined with ``node``, the type acts as a neighbourhood
      constraint rather than a node filter.
    - ``predicate``: keep only edges with this predicate; nodes are then
      restricted to their endpoints.
    - ``min_confidence``: drop edges below the threshold (nodes recomputed
      from surviving edges).
    - ``node`` + ``depth``: ego neighbourhood (undirected expansion over
      ``depth`` hops, then edges induced among kept nodes).
    - ``version``: asserted against ``g.graph["version"]`` (graphs are
      version-scoped at build time, D-029).
    """
    if version is not None and g.graph.get("version") != version:
        raise ValueError(f"graph is version {g.graph.get('version')!r}, "
                         f"not {version!r}")

    if node is not None:
        if node not in g:
            raise ValueError(f"node {node!r} not in graph")
        if depth is not None and depth < 0:
            raise ValueError("depth must be >= 0")
        keep = {node}
        frontier = {node}
        for _ in range(depth or 0):
            nxt: set[str] = set()
            for n in frontier:
                nxt |= set(nx.all_neighbors(g, n))
            nxt -= keep
            if entity_type:
                nxt = {n for n in nxt
                       if g.nodes[n].get(ATTR_TYPE) == entity_type}
            keep |= nxt
            frontier = nxt
        if entity_type and node not in keep:
            keep.discard(node)
    else:
        keep = {n for n, d in g.nodes(data=True)
                if entity_type is None or d.get(ATTR_TYPE) == entity_type}

    def edge_ok(u: str, v: str, d: dict) -> bool:
        if u not in keep or v not in keep:
            return False
        if predicate is not None and d.get(ATTR_PREDICATE) != predicate:
            return False
        if (min_confidence is not None
                and float(d.get(ATTR_CONFIDENCE, 0.0)) < min_confidence):
            return False
        return True

    sub = nx.MultiDiGraph()
    sub.graph.update(g.graph)
    sub.add_nodes_from((n, dict(g.nodes[n])) for n in keep if n in g)
    for u, v, k, d in g.edges(keys=True, data=True):
        if edge_ok(u, v, d):
            sub.add_edge(u, v, key=k, **dict(d))
    return sub
