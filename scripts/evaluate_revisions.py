#!/usr/bin/env python
"""
Score the M7 revision comparison against the M0 ground truth (M7.26).

    python scripts/evaluate_revisions.py            # human-readable report
    python scripts/evaluate_revisions.py --json     # machine-readable
    python scripts/evaluate_revisions.py --depth 2  # deeper impact scoring
    python scripts/evaluate_revisions.py --save     # write data/evaluation/

Compares the deterministic comparator output for
1.0.0 -> 1.1.0 against the mechanical gold derived from the ground-truth
registries + ``expected_diff``. Prose-only defects are reported as
not-applicable with reasons (never counted as false negatives).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import EVAL_DIR
from backend.diff.evaluation import evaluate_revisions
from backend.storage.database import make_engine, make_session_factory


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-version", default="1.0.0")
    ap.add_argument("--target-version", default="1.1.0")
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--save", action="store_true",
                    help="persist the report under data/evaluation/")
    args = ap.parse_args()

    sf = make_session_factory(make_engine())
    s = sf()
    ev = evaluate_revisions(s, args.base_version, args.target_version,
                            depth=args.depth)
    s.close()
    report = ev.to_dict()

    if args.save:
        EVAL_DIR.mkdir(parents=True, exist_ok=True)
        out = EVAL_DIR / "revision_evaluation.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"saved: {out}")

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"Revision evaluation: v{report['base_version']} -> "
          f"v{report['target_version']} (impact depth {args.depth})")
    print()
    hdr = f"{'change family':<24}{'exp':>5}{'det':>5}{'TP':>5}{'FP':>5}{'FN':>5}{'P':>8}{'R':>8}{'F1':>8}"
    print(hdr)
    print("-" * len(hdr))
    for fam in ("entity_added", "entity_removed", "entity_changed",
                "relationship_added", "relationship_removed",
                "stale_reference", "impact"):
        m = report[fam]
        print(f"{fam:<24}{m['expected']:>5}{m['detected']:>5}{m['tp']:>5}"
              f"{m['fp']:>5}{m['fn']:>5}{m['precision']:>8.3f}"
              f"{m['recall']:>8.3f}{m['f1']:>8.3f}")
    print()
    print("Detected counts:", json.dumps(report["detected_counts"]))
    print("Gold consistency checks:",
          json.dumps(report["gold_consistency"]))
    print()
    print("Planted-defect verdicts:")
    for d in report["defects"]:
        line = f"  {d['defect_id']:<40} {d['status']}"
        if d.get("detected") is not None:
            line += f"  detected={d['detected']}"
        if d.get("impact_reaches_source_component") is not None:
            line += f"  impact_src={d['impact_reaches_source_component']}"
        if d.get("impact_reaches_new_consumer") is not None:
            line += f"  impact_new_consumer={d['impact_reaches_new_consumer']}"
        print(line)
        if d["status"] == "not_applicable":
            print(f"      reason: {d['reason']}")
    print()
    t = report["timings_ms"]
    print(f"Timings (ms): compare={t['total_ms']} "
          f"evaluation={t['evaluation_ms']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
