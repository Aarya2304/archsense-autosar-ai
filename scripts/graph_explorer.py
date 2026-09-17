#!/usr/bin/env python
"""
Explore the M5 architecture graph (built from the M4 SQLite registry).

    python scripts/graph_explorer.py --version 1.0.0 --stats
    python scripts/graph_explorer.py --version 1.0.0 --related C-02
    python scripts/graph_explorer.py --version 1.0.0 --path C-02 IF-01
    python scripts/graph_explorer.py --version 1.0.0 --predicate requires
    python scripts/graph_explorer.py --version 1.0.0 --type component
    python scripts/graph_explorer.py --version 1.0.0 --confidence 0.9
    python scripts/graph_explorer.py --version 1.0.0 --render
    python scripts/graph_explorer.py --version 1.0.0 --json
    python scripts/graph_explorer.py --version 1.0.0 --export-graphml

Offline and deterministic: the graph is derived entirely from the local
registry; no LLM, no network. Run scripts/extract_entities.py first if the
registry is empty.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import EXPORTS_DIR, GRAPHS_DIR  # noqa: E402
from backend.graph import analysis  # noqa: E402
from backend.graph.service import GraphService  # noqa: E402


def _fact_lines(rel: dict, limit: int) -> list[str]:
    lines = []
    for pred, rows in rel["relationships"].items():
        for r in rows[:limit]:
            p = r["provenance"]
            lines.append(
                f"  {r['subject']} -[{pred}]-> {r['object']}   "
                f"conf={r['confidence']:.2f}  "
                f"{p.get('document_name', '?')} s{p.get('section_no', '?')} "
                f"p{p.get('page_start', '?')} chunk "
                f"{str(p.get('source_chunk_id', '?'))[:12]}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True, help="version label, e.g. 1.0.0")
    ap.add_argument("--stats", action="store_true", help="graph statistics")
    ap.add_argument("--related", metavar="ID",
                    help="relationships touching an entity (C-02 or key form)")
    ap.add_argument("--path", nargs=2, metavar=("SRC", "DST"),
                    help="shortest path between two entities")
    ap.add_argument("--undirected-path", action="store_true",
                    help="use undirected semantics for --path")
    ap.add_argument("--predicate", metavar="P",
                    help="filter to one predicate (subgraph stats)")
    ap.add_argument("--type", dest="etype", metavar="T",
                    help="filter to one entity type (subgraph stats)")
    ap.add_argument("--confidence", type=float, metavar="X",
                    help="filter to edges with confidence >= X")
    ap.add_argument("--node", metavar="KEY", default=None,
                    help="ego neighbourhood root for filtered stats "
                         "(use with --depth)")
    ap.add_argument("--depth", type=int, default=None,
                    help="neighbourhood depth for --node")
    ap.add_argument("--render", action="store_true",
                    help="render pyvis HTML to data/exports/")
    ap.add_argument("--json", action="store_true",
                    help="export node-link JSON to data/graphs/ and print stats")
    ap.add_argument("--export-graphml", action="store_true",
                    help="export GraphML to data/exports/")
    ap.add_argument("--limit", type=int, default=8,
                    help="rows shown per relationship group")
    args = ap.parse_args()

    if not (args.stats or args.related or args.path or args.predicate
            or args.etype or args.confidence is not None or args.render
            or args.json or args.export_graphml or args.node):
        ap.print_help()
        return 1

    svc = GraphService()
    g, stats = svc.build_graph(args.version)
    out: dict = {}

    if args.stats:
        out["statistics"] = stats.to_dict()

    # ---- filters (applied in order; each shows the subgraph summary) ----
    sub = g
    filtered = False
    if args.predicate:
        sub = svc.filter_graph(sub, predicate=args.predicate)
        filtered = True
    if args.etype:
        sub = svc.filter_graph(sub, entity_type=args.etype)
        filtered = True
    if args.confidence is not None:
        sub = svc.filter_graph(sub, min_confidence=args.confidence)
        filtered = True
    if args.node:
        key = analysis.resolve_key(sub, args.node)
        if key is None:
            print(f"error: cannot resolve node {args.node!r}", file=sys.stderr)
            return 1
        sub = svc.filter_graph(sub, node=key, depth=args.depth or 1)
        filtered = True
    if filtered:
        out["filtered"] = {
            "nodes": sub.number_of_nodes(),
            "edges": sub.number_of_edges(),
            "nodes_by_type": analysis.nodes_by_type(sub),
            "edges_by_predicate": analysis.edges_by_predicate(sub),
        }

    if args.related:
        key = analysis.resolve_key(g, args.related)
        if key is None:
            print(f"error: cannot resolve {args.related!r}", file=sys.stderr)
            return 1
        rel = analysis.related(g, key)
        out["related"] = {"key": key, "total": rel["total"]}

    if args.path:
        s = analysis.resolve_key(g, args.path[0])
        t = analysis.resolve_key(g, args.path[1])
        if s is None or t is None:
            print(f"error: cannot resolve {args.path}", file=sys.stderr)
            return 1
        p = analysis.shortest_path(g, s, t,
                                   directed=not args.undirected_path)
        out["path"] = {"source": s, "target": t,
                       "directed": not args.undirected_path,
                       "path": p}

    if args.json:
        from backend.graph.export import export_json
        p = export_json(sub if filtered else g,
                        GRAPHS_DIR / f"archsense_graph_v{args.version}.json",
                        stats)
        out["json_export"] = str(p)

    if args.export_graphml:
        from backend.graph.export import export_graphml
        p = export_graphml(sub if filtered else g,
                           EXPORTS_DIR /
                           f"archsense_graph_v{args.version}.graphml")
        out["graphml_export"] = str(p)

    if args.render:
        from backend.graph.visualization import render_html
        p = render_html(
            sub if filtered else g,
            EXPORTS_DIR / f"archsense_graph_v{args.version}.html")
        out["html_export"] = str(p)

    # ------------------------------ printing ------------------------------
    if args.stats:
        s = stats
        print(f"== graph v{s.version} ==")
        print(f"  nodes: {s.node_count}  edges: {s.edge_count}  "
              f"(build {s.build_time_ms:.1f} ms)")
        print("  nodes by type: "
              + ", ".join(f"{k}={v}" for k, v in s.nodes_by_type.items()))
        print("  edges by predicate: "
              + ", ".join(f"{k}={v}" for k, v in s.edges_by_predicate.items()))
    if filtered:
        f = out["filtered"]
        print(f"== filtered subgraph: {f['nodes']} nodes, {f['edges']} edges")
        print("  nodes by type: "
              + ", ".join(f"{k}={v}" for k, v in f["nodes_by_type"].items()))
        print("  edges by predicate: "
              + ", ".join(f"{k}={v}" for k, v in f["edges_by_predicate"].items()))
    if args.related:
        rel = analysis.related(g, analysis.resolve_key(g, args.related))
        print(f"== related: {rel['key']} ({rel['type']}) — "
              f"{rel['total']} relationships")
        for line in _fact_lines(rel, args.limit):
            print(line)
        if rel["total"] > args.limit * len(rel["relationships"]):
            print(f"  ... ({args.limit} shown per predicate; use --limit)")
    if args.path:
        p = out["path"]["path"]
        print("== path: "
              + (" -> ".join(p) if p else "(no path)"))
    for k in ("json_export", "graphml_export", "html_export"):
        if k in out:
            print(f"== {k}: {out[k]}")

    if args.json:
        (GRAPHS_DIR).mkdir(parents=True, exist_ok=True)
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
