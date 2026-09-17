"""Graph analysis (M5.10/M5.11): deterministic queries over the built graph.

Directed semantics are explicit: ``predecessors``/``successors``/
``shortest_path`` (default) use edge direction; helpers documented as
undirected (``neighbors_undirected``, ``weakly_connected_components``)
state so in their names/docs. No scores, no embeddings — pure structure.
"""

from __future__ import annotations

import networkx as nx

from backend.graph.models import ATTR_CONFIDENCE, ATTR_NAME, ATTR_TYPE

from collections import Counter


def node_degree(g: nx.MultiDiGraph, key: str) -> dict[str, int]:
    """Degree views: ``degree`` counts all edge endpoints (undirected view),
    in/out use direction."""
    return {
        "degree": int(g.degree(key)),
        "in_degree": int(g.in_degree(key)),
        "out_degree": int(g.out_degree(key)),
    }


def neighbors(g: nx.MultiDiGraph, key: str) -> dict[str, list[str]]:
    """Directed neighbors of ``key`` (successors + predecessors)."""
    return {
        "successors": sorted(g.successors(key)),
        "predecessors": sorted(g.predecessors(key)),
    }


def neighbors_undirected(g: nx.MultiDiGraph, key: str) -> list[str]:
    """All adjacent nodes regardless of direction (explicitly undirected)."""
    return sorted(nx.all_neighbors(g, key))


def predecessors(g: nx.MultiDiGraph, key: str) -> list[str]:
    return sorted(g.predecessors(key))


def successors(g: nx.MultiDiGraph, key: str) -> list[str]:
    return sorted(g.successors(key))


def shortest_path(g: nx.MultiDiGraph, source: str, target: str,
                  directed: bool = True) -> list[str] | None:
    """Shortest node path (BFS, unweighted). Directional by default; pass
    ``directed=False`` for the undirected view. Returns None when no path."""
    try:
        if directed:
            return nx.shortest_path(g, source, target)
        return nx.shortest_path(g.to_undirected(as_view=True), source, target)
    except nx.NetworkXNoPath:
        return None
    except nx.NodeNotFound:
        return None


def weakly_connected_components(g: nx.MultiDiGraph,
                                min_size: int = 1) -> list[list[str]]:
    """Weakly connected components (undirected semantics), each sorted,
    components ordered largest first. ``min_size`` filters tiny fragments
    (orphans) for M6-style orphan surfacing."""
    comps = [sorted(c) for c in nx.weakly_connected_components(g)
             if len(c) >= min_size]
    comps.sort(key=lambda c: (-len(c), c[0]))
    return comps


def edges_by_predicate(g: nx.MultiDiGraph) -> dict[str, int]:
    c: Counter[str] = Counter(d.get("predicate", "?")
                              for _, _, d in g.edges(data=True))
    return dict(sorted(c.items()))


def nodes_by_type(g: nx.MultiDiGraph) -> dict[str, int]:
    c: Counter[str] = Counter(d.get(ATTR_TYPE, "?")
                              for _, d in g.nodes(data=True))
    return dict(sorted(c.items()))


def related(g: nx.MultiDiGraph, key: str) -> dict:
    """Structured relatedness view for one entity (M5.11).

    Groups touching edges by predicate, preserving M4 edge identity
    (fact_key) and trusted provenance on every relationship.
    """
    out: dict[str, list[dict]] = {}
    for u, v, k, d in g.edges(keys=True, data=True):
        if key not in (u, v):
            continue
        direction = "out" if u == key else "in"
        out.setdefault(d.get("predicate", "?"), []).append({
            "direction": direction,
            "subject": u,
            "object": v,
            "fact_key": k,
            "confidence": d.get(ATTR_CONFIDENCE, 0.0),
            "other": v if u == key else u,
            "name": g.nodes[u if u == key else v].get(ATTR_NAME, ""),
            "type": g.nodes[u if u == key else v].get(ATTR_TYPE, ""),
            "provenance": d.get("provenance", {}),
        })
    for group in out.values():
        group.sort(key=lambda r: (r["direction"], r["fact_key"]))
    return {"key": key,
            "type": g.nodes[key].get(ATTR_TYPE, "?") if key in g else "?",
            "name": g.nodes[key].get(ATTR_NAME, "") if key in g else "",
            "relationships": dict(sorted(out.items())),
            "total": sum(len(v) for v in out.values())}


def resolve_key(g: nx.MultiDiGraph, identifier: str) -> str | None:
    """Resolve 'C-02' / 'component:C-02' / 'component:c-02' / display name
    to exactly one node key (deterministic; ambiguous names -> None)."""
    ident = identifier.strip()
    if ident in g:
        return ident
    up = ident.upper()
    if ":" in ident:
        etype, _, eid = ident.partition(":")
        cand = f"{etype.lower()}:{eid.upper()}"
        if cand in g:
            return cand
        return None
    for etype in ("component", "interface", "port", "signal", "dependency",
                  "functional_flow"):
        cand = f"{etype}:{up}"
        if cand in g:
            return cand
    # display-name lookup (case-insensitive, must be unique)
    matches = sorted(k for k, d in g.nodes(data=True)
                     if str(d.get(ATTR_NAME, "")).lower() == ident.lower())
    return matches[0] if len(matches) == 1 else None
