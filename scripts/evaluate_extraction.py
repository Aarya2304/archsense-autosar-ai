#!/usr/bin/env python
"""
Run the M4 extraction evaluation against the M0 ground truth.

    python scripts/evaluate_extraction.py            # writes + prints the report
    python scripts/evaluate_extraction.py --json     # machine-readable only

Output: data/evaluation/extraction_evaluation.json (git-ignored).
Gold entities/facts are derived mechanically from the ground-truth
registries, so no numbers are fabricated; per-example detail is included
for the two smallest predicate families.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import EVAL_DIR, PROCESSED_DIR  # noqa: E402
from backend.dataset.ground_truth import load_ground_truth  # noqa: E402
from backend.extraction.evaluation import run_evaluation, save_report  # noqa: E402

REPORT_PATH = EVAL_DIR / "extraction_evaluation.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true",
                    help="print the raw JSON report only")
    ap.add_argument("--min-confidence", type=float, default=0.0)
    args = ap.parse_args()

    gt = load_ground_truth()
    processed = {
        "1.0.0": PROCESSED_DIR / "ABC_HLD_v1.0.0__processed.json",
        "1.1.0": PROCESSED_DIR / "ABC_HLD_v1.1.0__processed.json",
    }
    for ver, pj in processed.items():
        if not pj.exists():
            print(f"missing {pj}; run scripts/process_sample_docs.py first",
                  file=sys.stderr)
            return 1

    report = run_evaluation(processed, gt,
                            min_confidence=args.min_confidence)
    out = save_report(report, REPORT_PATH)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"Extraction evaluation -> {out}\n")
    for ver, r in report["versions"].items():
        e, f = r["entities"], r["facts"]
        print(f"=== HLD v{ver} ===")
        print(f"  entities  : P={e['precision']:.3f} R={e['recall']:.3f} "
              f"F1={e['f1']:.3f}  (tp={e['tp']} fp={e['fp']} fn={e['fn']})")
        print(f"  facts     : P={f['precision']:.3f} R={f['recall']:.3f} "
              f"F1={f['f1']:.3f}  (tp={f['tp']} fp={f['fp']} fn={f['fn']})")
        print("  by predicate:")
        for pred, s in r["facts_by_predicate"].items():
            print(f"    {pred:<16} P={s['precision']:.3f} "
                  f"R={s['recall']:.3f} F1={s['f1']:.3f} "
                  f"(tp={s['tp']} fp={s['fp']} fn={s['fn']})")
        print(f"  provenance accuracy: {r['provenance_accuracy']:.3f}")
        print(f"  duplicate facts merged: {r['duplicate_facts_merged']}")
        print(f"  validation issues   : {r['validation_issues']}")
        print(f"  total latency       : {r['timings_ms']['total_ms']} ms")
        for pred, d in r.get("per_example_detail", {}).items():
            print(f"  [{pred}] missed={len(d['missed'])} "
                  f"spurious={len(d['spurious'])}")
            for m in d["missed"][:3]:
                print(f"    missed : {m[0]} -{m[1]}-> {m[2]}")
            for s in d["spurious"][:3]:
                print(f"    spurious: {s[0]} -{s[1]}-> {s[2]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
