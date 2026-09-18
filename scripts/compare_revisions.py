#!/usr/bin/env python
"""
Deterministic v1 -> v2 revision comparison + impact analysis (M7).

    python scripts/compare_revisions.py --base-version 1.0.0 --target-version 1.1.0
    python scripts/compare_revisions.py --base-version 1.0.0 --target-version 1.1.0 --depth 2
    python scripts/compare_revisions.py --base-version 1.0.0 --target-version 1.1.0 --json
    python scripts/compare_revisions.py ... --persist          # cache in CompareRun
    python scripts/compare_revisions.py ... --change-type entity_removed
    python scripts/compare_revisions.py ... --max-changes 25   # trim listings

Both version labels are required (D-036: base == target is rejected;
versions from different projects are rejected as unrelated documents).
Fully offline: no LLM, no network, no PDF parsing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.diff.comparator import RevisionComparator
from backend.diff.persistence import (clear_comparison, load_comparison,
                                      persist_comparison)
from backend.diff.validation import validate_comparison
from backend.storage.database import make_engine, make_session_factory

CHANGE_LABELS = {
    "entity_added": "ENTITY_ADDED",
    "entity_removed": "ENTITY_REMOVED",
    "entity_changed": "ENTITY_CHANGED",
    "relationship_added": "RELATIONSHIP_ADDED",
    "relationship_removed": "RELATIONSHIP_REMOVED",
}


def _print_change(c) -> None:
    prov = c.provenance[0].to_dict() if c.provenance else {}
    where = (f"{prov.get('document_name', '?')} "
             f"sec {prov.get('section_no', '?') or '?'}"
             f"{' ' + prov.get('section_title', '') if prov.get('section_title') else ''}"
             f", p.{prov.get('pages_csv', '?')}")
    if hasattr(c, "entity_key"):
        label = getattr(c, "display_name", "") or c.entity_key
        extra = ""
        if c.change_type.value == "entity_changed":
            extra = f" ({c.base_name} -> {c.target_name})"
        print(f"    [{c.change_id}] {c.entity_key} {label!r}{extra}  |  {where}")
    else:
        print(f"    [{c.change_id}] {c.subject} --{c.predicate}--> "
              f"{c.object}  |  {where}")


def _print_impact(i, limit_paths: bool = True) -> None:
    path_txt = ""
    if i.path and limit_paths:
        path_txt = "  path: " + " -> ".join(s.render() for s in i.path)
    print(f"    [{i.impact_id}] d{i.depth} {i.category.value:<18} "
          f"{i.impacted_entity_key}{path_txt}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-version", required=True)
    ap.add_argument("--target-version", required=True)
    ap.add_argument("--depth", type=int, default=1,
                    help="impact traversal depth (default 1, conservative)")
    ap.add_argument("--json", action="store_true",
                    help="emit the full machine-readable comparison")
    ap.add_argument("--persist", action="store_true",
                    help="upsert the comparison into compare_runs (idempotent)")
    ap.add_argument("--reset", action="store_true",
                    help="delete persisted comparisons for this pair, then exit")
    ap.add_argument("--change-type", choices=sorted(CHANGE_LABELS),
                    help="show only this change type in the listing")
    ap.add_argument("--max-changes", type=int, default=40,
                    help="max changes printed per section (0 = all)")
    ap.add_argument("--no-impact-list", action="store_true",
                    help="skip printing individual impacts (summary only)")
    args = ap.parse_args()

    sf = make_session_factory(make_engine())
    if args.reset:
        s = sf()
        n = clear_comparison(s, args.base_version, args.target_version)
        s.close()
        print(f"Cleared {n} persisted comparison(s) for "
              f"{args.base_version} -> {args.target_version}")
        return 0

    try:
        cmp = RevisionComparator().compare(args.base_version,
                                           args.target_version,
                                           depth=args.depth)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.persist:
        s = sf()
        info = persist_comparison(s, cmp, depth=args.depth)
        s.close()
    if args.json:
        print(cmp.to_json())
        return 0

    s = cmp.summary
    print(f"Base version:   {cmp.base_version}")
    print(f"Target version: {cmp.target_version}")
    print(f"Document:       {cmp.document_name}")
    print()
    print("Entity changes:")
    print(f"  added: {s.entity_added}  removed: {s.entity_removed}  "
          f"changed: {s.entity_changed}")
    print("Relationship changes:")
    print(f"  added: {s.relationship_added}  removed: "
          f"{s.relationship_removed}")
    print()
    print(f"By predicate: {json.dumps(s.relationship_changes_by_predicate)}")
    print(f"Entity changes by type: "
          f"{json.dumps(s.entity_changes_by_type)}")
    print(f"Impacts: {s.impact_count}  "
          f"by category: {json.dumps(s.impacts_by_category)}")
    print(f"Revision findings: {s.revision_finding_count}")
    if args.persist:
        print(f"Persisted: compare_run_id={info['compare_run_id']} "
              f"({list(k for k in info if k != 'compare_run_id')[0]})")
    print()

    show = {args.change_type} if args.change_type else None
    limit = args.max_changes

    def want(t: str) -> bool:
        return show is None or t in show

    def emit(rows, label: str) -> None:
        rows = list(rows)
        if not want(label) or not rows:
            return
        print(f"{CHANGE_LABELS[label]} ({len(rows)}):")
        for c in rows[:limit] if limit else rows:
            _print_change(c)
        if limit and len(rows) > limit:
            print(f"    ... {len(rows) - limit} more")
        print()

    emit((c for c in cmp.entity_changes
          if c.change_type.value == "entity_added"), "entity_added")
    emit((c for c in cmp.entity_changes
          if c.change_type.value == "entity_removed"), "entity_removed")
    emit((c for c in cmp.entity_changes
          if c.change_type.value == "entity_changed"), "entity_changed")
    emit((c for c in cmp.relationship_changes
          if c.change_type.value == "relationship_added"),
         "relationship_added")
    emit((c for c in cmp.relationship_changes
          if c.change_type.value == "relationship_removed"),
         "relationship_removed")

    if not args.no_impact_list and args.change_type is None:
        print(f"Potential impacts (depth {args.depth}, first 15 of "
              f"{s.impact_count}; full list in --json):")
        for i in cmp.impacts[:15]:
            _print_impact(i)
        print()

    if cmp.revision_findings and args.change_type is None:
        print("Revision findings:")
        for rf in cmp.revision_findings:
            print(f"    [{rf.finding_id}] ({rf.severity}) {rf.title}")
            for p in rf.provenance:
                print(f"        {p.to_dict()}")
        print()

    print(f"Validation: {'PASS' if cmp.validation['valid'] else 'FAIL'}"
          f" ({len(cmp.validation['errors'])} errors, "
          f"{len(cmp.validation['warnings'])} warnings)")
    print(f"Timings (ms): {json.dumps(cmp.timings_ms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
