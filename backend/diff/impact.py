"""Impact analysis (M7.10-13): deterministic graph-neighborhood traversal.

Rules (D-038; task rules 10-13):

- Anchors: for ENTITY_* changes the changed entity; for RELATIONSHIP_*
  changes both fact endpoints. The graph side follows the change: ADDED
  impacts are traversed in the TARGET graph, REMOVED in the BASE graph,
  ENTITY_CHANGED in both.
- Depth 0 = the anchor itself (category DIRECT); depth 1 neighbors inherit
  the category of the first edge used (DEPENDENCY / INTERFACE_CONSUMER /
  INTERFACE_PROVIDER / SIGNAL / FUNCTIONAL_FLOW); depth >= 2 is TRANSITIVE.
- Traversal is a breadth-first walk over REAL graph edges (MultiDiGraph,
  both directions — impact includes consumers and providers alike); every
  recorded path step is an edge that exists in that graph. No invented
  hops, no LLM.
- Wording is 'potentially impacted' — a graph-neighborhood statement, not
  a claim of functional breakage (task rule 11).

Determinism: edges are visited sorted by fact_key; the first (shortest)
path to a node wins and impacts dedupe on (change, entity, depth).
"""

from __future__ import annotations

from collections import deque

from backend.diff.models import (ChangeType, ImpactCategory, ImpactItem,
                                 PathStep, category_for_predicate,
                                 deterministic_impact_id)
from backend.diff.context import VersionSnapshot


def _sorted_adjacent(g, node: str):
    """All (neighbor, predicate, fact_key, direction) edges of a node.

    Deterministic order (sorted by fact_key, then neighbor); direction is
    'out' for node->neighbor and 'in' for neighbor->node.
    """
    rows = []
    for u, v, k in g.out_edges(node, keys=True):
        fk = g.edges[u, v, k].get("fact_key", k)
        rows.append((v, g.edges[u, v, k].get("predicate", ""), fk, "out"))
    for u, v, k in g.in_edges(node, keys=True):
        fk = g.edges[u, v, k].get("fact_key", k)
        rows.append((u, g.edges[u, v, k].get("predicate", ""), fk, "in"))
    rows.sort(key=lambda r: (r[2], r[0], r[3]))
    return rows


def _traverse(g, anchors: list[str], max_depth: int,
              version_scope: str, source_change_id: str,
              impacted_type_of) -> list[ImpactItem]:
    """BFS from the anchors; one ImpactItem per entity at its min depth."""
    impacts: list[ImpactItem] = []
    queue: deque[tuple[str, int, tuple[PathStep, ...]]] = deque()
    for a in anchors:
        if a in g:
            queue.append((a, 0, ()))
    best: dict[tuple[str, int], tuple[PathStep, ...]] = {}
    seen_depth: dict[str, int] = {}

    while queue:
        node, depth, path = queue.popleft()
        if (node, depth) in best:
            continue
        best[(node, depth)] = path

        if depth == 0:
            cat = ImpactCategory.DIRECT
            reason = ("directly involved in this change "
                      "(changed entity or relationship endpoint)")
        else:
            first = path[0] if path else None
            cat = (category_for_predicate(first.predicate) if depth == 1
                   and first is not None else ImpactCategory.TRANSITIVE)
            edge_desc = (f"first edge {first.render()}" if first else "?")
            reason = (f"potentially impacted: connected at depth {depth} "
                      f"via {edge_desc}")
        impacts.append(ImpactItem(
            impact_id=deterministic_impact_id(source_change_id, node, depth),
            source_change_id=source_change_id,
            impacted_entity_key=node,
            entity_type=impacted_type_of(node),
            category=cat, reason=reason,
            path=list(path), depth=depth, version_scope=version_scope))
        seen_depth[node] = depth

        if depth >= max_depth:
            continue
        for neighbor, pred, fk, direction in _sorted_adjacent(g, node):
            # record every step in WALK order with its true direction so
            # paths stay connected and every step is a real stored edge
            if direction == "out":
                step = PathStep(frm=node, predicate=pred, to=neighbor,
                                fact_key=fk, direction="forward")
            else:
                step = PathStep(frm=node, predicate=pred, to=neighbor,
                                fact_key=fk, direction="reverse")
            nkey = (neighbor, depth + 1)
            if nkey not in best:
                queue.append((neighbor, depth + 1, path + (step,)))

    # prune deeper re-reaches: keep only each node's minimum-depth item so
    # a (change, entity) pair maps to exactly one impact (min-depth BFS
    # order + V9 uniqueness)
    min_items: dict[str, ImpactItem] = {}
    for imp in impacts:
        cur = min_items.get(imp.impacted_entity_key)
        if cur is None or (imp.depth, imp.sort_key()) < \
                (cur.depth, cur.sort_key()):
            min_items[imp.impacted_entity_key] = imp
    return sorted(min_items.values(), key=lambda i: i.sort_key())


def _anchors_for(change, base: VersionSnapshot,
                 target: VersionSnapshot) -> tuple[list[str], VersionSnapshot,
                                                   str]:
    """(anchor keys, graph to traverse, scope version) for one change."""
    if change.change_type in (ChangeType.ENTITY_ADDED,):
        return [change.entity_key], target, target.version_label
    if change.change_type in (ChangeType.ENTITY_REMOVED,):
        return [change.entity_key], base, base.version_label
    if change.change_type == ChangeType.ENTITY_CHANGED:
        return [change.entity_key], target, target.version_label
    # relationship changes: anchor both endpoints on the side where the
    # triple lives (added -> target, removed -> base)
    snap = target if (change.change_type
                      == ChangeType.RELATIONSHIP_ADDED) else base
    anchors = [k for k in (change.subject, change.object) if k]
    return anchors, snap, snap.version_label


def analyze_impacts(entity_changes, relationship_changes,
                    base: VersionSnapshot, target: VersionSnapshot,
                    max_depth: int = 1) -> list[ImpactItem]:
    """Compute all impact items for a comparison (task rules 10-12)."""
    out: list[ImpactItem] = []

    def typeof(snap: VersionSnapshot):
        def _t(node: str) -> str:
            row = snap.entities.get(node)
            if row is not None:
                return node.split(":", 1)[0]
            return node.split(":", 1)[0] if ":" in node else "?"
        return _t

    for ch in entity_changes:
        anchors, snap, scope = _anchors_for(ch, base, target)
        out.extend(_traverse(snap.graph, anchors, max_depth, scope,
                             ch.change_id, typeof(snap)))
    for ch in relationship_changes:
        anchors, snap, scope = _anchors_for(ch, base, target)
        out.extend(_traverse(snap.graph, anchors, max_depth, scope,
                             ch.change_id, typeof(snap)))

    out.sort(key=lambda i: i.sort_key())
    return out
