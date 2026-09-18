#!/usr/bin/env python
"""Run deterministic architecture analysis (M6) over one HLD version.

    python scripts/analyze_findings.py --version 1.0.0
    python scripts/analyze_findings.py --version 1.1.0 --persist
    python scripts/analyze_findings.py --version 1.1.0 --json
    python scripts/analyze_findings.py --version 1.1.0 --finding-type orphan_entity
    python scripts/analyze_findings.py --version 1.1.0 --severity medium --reset

Pipeline: SQLite M4 registry + M5 graph -> six deterministic detectors ->
mechanical validation -> (optional) idempotent persistence. No LLM, no
network, no PDF parsing. Print output is a concise summary; --json emits
the full structured result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DB_PATH                                    # noqa: E402
from backend.findings.engine import FindingEngine                     # noqa: E402
from backend.findings.persistence import (begin_analysis_run,         # noqa: E402
                                          clear_findings,
                                          finish_analysis_run,
                                          persist_findings,
                                          version_id_for)
from backend.storage.database import make_engine, make_session_factory  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--version", required=True,
                    help="document version label, e.g. 1.0.0")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of text")
    ap.add_argument("--persist", action="store_true",
                    help="persist findings to the SQLite registry (idempotent)")
    ap.add_argument("--reset", action="store_true",
                    help="delete persisted analysis findings for the version first")
    ap.add_argument("--finding-type", action="append", default=[],
                    help="restrict to a finding type (repeatable)")
    ap.add_argument("--severity", help="restrict to a severity (high|medium|low|info)")
    args = ap.parse_args()

    if args.reset and not args.persist:
        print("--reset requires --persist", file=sys.stderr)
        return 2

    session = make_session_factory(make_engine(DB_PATH))()
    try:
        engine = FindingEngine()
        res = engine.run(session, args.version,
                         finding_types=args.finding_type or None)
        findings = engine.filter_findings(res.findings, None, args.severity)

        if args.reset:
            n = clear_findings(session, version_id_for(session, args.version))
            if not args.json:
                print(f"reset: removed {n} persisted finding rows")

        persist_stats = None
        if args.persist:
            persist_stats = persist_findings(
                session, version_id_for(session, args.version),
                findings if (args.finding_type or args.severity)
                else res.findings,
                params={"finding_types": args.finding_type,
                        "severity": args.severity})

        if args.json:
            print(json.dumps({
                "version": res.version_label,
                "entities_analyzed": res.entity_count,
                "facts_analyzed": res.fact_count,
                "detectors": res.detectors_run,
                "validation": {"ok": res.validation_ok,
                               "issues": res.validation_issues},
                "counts": {"by_type": res.by_type(),
                           "by_severity": res.by_severity()},
                "persist": persist_stats,
                "timings_ms": res.timings_ms,
                "findings": [f.to_dict() for f in findings],
            }, indent=2, ensure_ascii=False))
            return 0

        # ---------------- text summary ----------------
        print(f"Version: {res.version_label}")
        print(f"Entities analyzed: {res.entity_count}")
        print(f"Facts analyzed:    {res.fact_count}")
        print(f"Findings: {len(findings)}")
        print()
        print("By type:")
        for ft in ("UNDEFINED_REFERENCE", "DANGLING_REQUIRES",
                   "DUPLICATE_INTERFACE", "CONFLICTING_PROVIDERS",
                   "ORPHAN_ENTITY", "UNCONSUMED_SIGNAL"):
            n = res.by_type().get(ft.lower(), 0)
            print(f"  {ft}: {n}")
        print()
        print("By severity:")
        for s, n in res.by_severity().items():
            print(f"  {s}: {n}")
        if persist_stats:
            print()
            print(f"Persisted: run_id={persist_stats['run_id']} "
                  f"persisted={persist_stats['persisted']} "
                  f"carried_review_state={persist_stats['updated']} "
                  f"removed={persist_stats['removed']}")
        if findings:
            print()
            for f in findings:
                prov = f.provenance[0] if f.provenance else {}
                loc = (f"{prov.get('section_no', '')} p{prov.get('page_start', '?')}"
                       if prov else "no provenance")
                print(f"[{f.severity.value}] {f.finding_id}")
                print(f"  {f.finding_type.value}: {f.title}")
                print(f"  {f.description}")
                print(f"  entities: {', '.join(f.entity_keys) or '-'}")
                print(f"  evidence: {len(f.evidence)} item(s), "
                      f"source: {prov.get('document_name') or '-'} {loc}")
                print()
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
