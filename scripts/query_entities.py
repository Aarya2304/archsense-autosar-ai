#!/usr/bin/env python
"""
Query the M4 extraction registry (SQLite) from the command line.

    python scripts/query_entities.py --entities                       # registry counts by type
    python scripts/query_entities.py --entity C-02                    # one entity + attributes
    python scripts/query_entities.py --related C-02                   # facts touching C-02
    python scripts/query_entities.py --predicate provides --limit 10  # filtered facts
    python scripts/query_entities.py --fact "component:C-02|requires|interface:IF-03"   # provenance trail
    python scripts/query_entities.py --version 1.0.0 ...              # scope to one version

Every fact row carries trusted provenance (document, version, section,
page range, chunk id), so --fact shows the full traceability trail:
fact -> chunk -> document -> version -> section -> page.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from backend.config import DB_PATH  # noqa: E402
from backend.storage.database import init_schema, make_engine, make_session_factory  # noqa: E402
from backend.storage.models import (AnalysisRun, Component, Dependency,  # noqa: E402
                                    ExtractionFact, FunctionalFlow,
                                    Interface, Port, Signal)

_REGISTRY_MODELS = {
    "component": Component,
    "interface": Interface,
    "port": Port,
    "signal": Signal,
    "dependency": Dependency,
    "functional_flow": FunctionalFlow,
}


def _session():
    engine = make_engine(DB_PATH)
    init_schema(engine)
    return make_session_factory(engine)()


def _dv_id(session, version: str | None) -> int | None:
    if not version:
        return None
    from backend.storage.models import DocumentVersion
    dv = session.execute(select(DocumentVersion).where(
        DocumentVersion.version_label == version)).scalars().first()
    return dv.id if dv else None


def _fact_row_dict(r) -> dict:
    return {
        "subject": r.subject, "predicate": r.predicate, "object": r.object,
        "confidence": r.confidence, "extractor": r.extractor,
        "document": r.document_name, "version": r.version_label,
        "section": (f"{r.section_no} {r.section_title}".strip()),
        "pages": (str(r.page_start) if r.page_start == r.page_end
                  else f"{r.page_start}-{r.page_end}"),
        "chunk_id": r.source_chunk_id,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--entities", action="store_true",
                    help="registry counts by entity type")
    ap.add_argument("--entity", metavar="ID",
                    help="show one entity (e.g. C-02, IF-03, SG-018)")
    ap.add_argument("--related", metavar="ID",
                    help="facts whose subject or object matches ID")
    ap.add_argument("--predicate", metavar="P",
                    help="filter facts by predicate")
    ap.add_argument("--fact", metavar="KEY",
                    help="show provenance for one fact key "
                         "(subject|predicate|object)")
    ap.add_argument("--version", default=None, help="scope to a version label")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not (args.entities or args.entity or args.related or args.predicate
            or args.fact):
        ap.print_help()
        return 1

    session = _session()
    out: dict = {}
    vid = _dv_id(session, args.version)

    if args.entities:
        counts = {}
        for etype, model in _REGISTRY_MODELS.items():
            q = select(func.count()).select_from(model)
            if vid:
                q = q.where(model.version_id == vid)
            counts[etype] = session.execute(q).scalar() or 0
        fq = select(func.count()).select_from(ExtractionFact)
        if vid:
            fq = fq.where(ExtractionFact.version_id == vid)
        counts["facts"] = session.execute(fq).scalar() or 0
        runs = select(AnalysisRun).where(AnalysisRun.kind == "extraction")
        if vid:
            runs = runs.where(AnalysisRun.version_id == vid)
        counts["extraction_runs"] = len(
            session.execute(runs).scalars().all())
        out["registry_counts"] = counts

    if args.entity:
        eid = args.entity.strip().upper()
        found = {}
        for etype, model in _REGISTRY_MODELS.items():
            q = select(model).where(model.entity_id == eid)
            if vid:
                q = q.where(model.version_id == vid)
            row = session.execute(q).scalars().first()
            if row is None:
                continue
            found[etype] = {
                col: getattr(row, col)
                for col in ("entity_id", "name", "type", "layer", "kind",
                            "provider_id", "component_id", "interface_id",
                            "direction", "datatype", "unit", "source_id",
                            "target_id", "relationship", "trigger",
                            "description")
                if hasattr(row, col) and getattr(row, col) not in ("", None)
            } | {"page": row.page, "section": row.section,
                 "confidence": row.confidence, "source": row.source}
        out["entity"] = found or f"no entity {eid!r} in registry"

    if args.related:
        rid = args.related.strip().upper()
        key_like = f"%:{rid}"
        q = select(ExtractionFact).where(
            (ExtractionFact.subject.like(key_like))
            | (ExtractionFact.object.like(key_like)))
        if vid:
            q = q.where(ExtractionFact.version_id == vid)
        rows = session.execute(q).scalars().all()
        out["related"] = [_fact_row_dict(r) for r in rows[:args.limit]]
        out["related_count"] = len(rows)

    if args.predicate:
        q = select(ExtractionFact).where(
            ExtractionFact.predicate == args.predicate.strip().lower())
        if vid:
            q = q.where(ExtractionFact.version_id == vid)
        rows = session.execute(q).scalars().all()
        out["predicate_facts"] = [_fact_row_dict(r) for r in
                                  rows[:args.limit]]
        out["predicate_count"] = len(rows)

    if args.fact:
        # fact_key stores subject|predicate|object|object_value; accept the
        # short user form (3 segments) by matching with the trailing segment.
        key = args.fact.strip()
        candidates = [key] if key.endswith("|") else [key, key + "|"]
        row = None
        for cand in candidates:
            q = select(ExtractionFact).where(
                ExtractionFact.fact_key == cand)
            if vid:
                q = q.where(ExtractionFact.version_id == vid)
            row = session.execute(q).scalars().first()
            if row is not None:
                break
        out["fact"] = _fact_row_dict(row) if row else \
            f"no fact {args.fact!r} in registry"

    session.close()
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        for section, payload in out.items():
            print(f"== {section} ==")
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        print("  " + json.dumps(item, default=str))
                    else:
                        print(f"  {item}")
            elif isinstance(payload, dict):
                for k, v in payload.items():
                    print(f"  {k}: {v}")
            else:
                print(f"  {payload}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
