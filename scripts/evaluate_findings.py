#!/usr/bin/env python
"""Evaluate M6 finding detection against the ground-truth defect catalogue.

    python scripts/evaluate_findings.py            # both versions, text
    python scripts/evaluate_findings.py --json     # machine-readable

Compares detector output against the M0 ``expected_findings`` ground truth.
Gold defects WITHOUT a structural witness in the persisted registry are
reported as not-applicable (with reasons) rather than counted as false
negatives — a registry-level detector cannot be honestly scored against a
prose-only or cross-version defect. No numbers are fabricated; every metric
is computed from the actual run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.findings.evaluation import evaluate_all                 # noqa: E402
from backend.storage.database import make_engine, make_session_factory  # noqa: E402
from backend.config import DB_PATH                                   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON")
    args = ap.parse_args()

    session = make_session_factory(make_engine(DB_PATH))()
    try:
        out = evaluate_all(session)
    finally:
        session.close()

    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    for vl, block in out["versions"].items():
        m = block["metrics"]
        print(f"===== v{vl}")
        print(f"  expected (applicable):    {m['expected_applicable']}")
        print(f"  expected (not applicable): {m['expected_not_applicable']}")
        print(f"  detected:                 {m['detected']}")
        print(f"  TP={m['tp']}  FP={m['fp']}  FN={m['fn']}")
        print(f"  precision={m['precision']:.4f}  recall={m['recall']:.4f}  "
              f"F1={m['f1']:.4f}")
        print("  per detector family:")
        for t, pt in block["per_type"].items():
            na = (f" [gold not applicable: {pt['gold_not_applicable']}]"
                  if pt["gold_not_applicable"] else "")
            print(f"    {t:24s} detected={pt['detected']} gold={pt['gold_applicable']} "
                  f"TP={pt['tp']} FP={pt['fp']} FN={pt['fn']}{na}")
        if block["not_applicable"]:
            print("  gold defects not applicable to registry-level M6:")
            for g in block["not_applicable"]:
                print(f"    - {g['defect_id']}: {g['reason']}")
        tm = block.get("timings_ms", {})
        print(f"  timing: context={tm.get('context_ms', 0):.1f} ms, "
              f"detectors+validation={tm.get('total_ms', 0):.1f} ms total")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
