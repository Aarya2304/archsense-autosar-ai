#!/usr/bin/env python
"""
Evaluate the M5 architecture graph against the M0 ground truth (M5.20).

    python scripts/evaluate_graph.py            # both versions, human report
    python scripts/evaluate_graph.py --json     # machine-readable output

Gold nodes/edges are derived mechanically from the ground-truth registries
(the same source-of-truth model that rendered the PDFs), so graph scores
are exact-set comparisons, not judgments. Writes
data/evaluation/graph_evaluation.json (git-ignored runtime artifact).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from backend.config import EVAL_DIR  # noqa: E402
from backend.extraction.evaluation import gold_entities, gold_facts  # noqa: E402
from backend.graph.evaluation import evaluate_graph  # noqa: E402
from backend.graph.service import GraphService  # noqa: E402
from backend.storage.database import init_schema, make_engine, make_session_factory  # noqa: E402
from backend.storage.models import ExtractionFact  # noqa: E402


def _registry_keys(session, version: str) -> set[str]:
    rows = session.execute(select(ExtractionFact).where(
        ExtractionFact.version_label == version)).scalars().all()
    return {r.fact_key for r in rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true",
                    help="print machine-readable JSON report")
    args = ap.parse_args()

    gt_path = Path("data/ground_truth/ground_truth.json")
    if not gt_path.exists():
        print("error: ground truth missing — run "
              "scripts/generate_dataset.py first", file=sys.stderr)
        return 1
    gt = json.loads(gt_path.read_text(encoding="utf-8"))

    engine = make_engine()
    init_schema(engine)
    factory = make_session_factory(engine)
    session = factory()
    svc = GraphService(session_factory=factory)

    report: dict = {"versions": {}}
    for ver, key in (("1.0.0", "v1"), ("1.1.0", "v2")):
        g, stats = svc.build_graph(ver)
        gt_v = gt["entities"][key]
        ports = gt["ports"][key]
        reg_keys = _registry_keys(session, ver)
        res = evaluate_graph(g, gt_v, ports, reg_keys)
        report["versions"][ver] = res.to_dict()

        print(f"=== HLD v{ver} ===")
        n, e = res.nodes, res.edges
        print(f"  nodes: P={n['precision']:.3f} R={n['recall']:.3f} "
              f"F1={n['f1']:.3f}  (expected={n['expected']} "
              f"actual={n['actual']} tp={n['tp']} fp={n['fp']} fn={n['fn']})")
        print(f"  edges: P={e['precision']:.3f} R={e['recall']:.3f} "
              f"F1={e['f1']:.3f}  (expected={e['expected']} "
              f"actual={e['actual']} tp={e['tp']} fp={e['fp']} fn={e['fn']})")
        for pred, s in res.edges_by_predicate.items():
            print(f"    {pred:<16} P={s['precision']:.3f} "
                  f"R={s['recall']:.3f} F1={s['f1']:.3f} "
                  f"(tp={s['tp']} fp={s['fp']} fn={s['fn']})")
        iso = res.version_isolation
        print(f"  version isolation: "
              f"{'clean' if iso['clean'] else 'CONTAMINATED'} "
              f"(foreign keys: {iso['foreign_count']})")
        print(f"  provenance correctness: {res.provenance_correctness:.3f}")
        it = res.integrity
        print(f"  integrity: registry={it['registry_facts']} "
              f"graph={it['graph_edges']} "
              f"missing={len(it['missing_from_graph'])} "
              f"extra={len(it['not_in_registry'])} "
              f"dup_keys={it['duplicate_fact_keys']}")
        print(f"  eval time: {res.timings_ms['eval_ms']:.1f} ms")
        print()
    session.close()

    out = EVAL_DIR / "graph_evaluation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report -> {out}")
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
