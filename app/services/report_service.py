"""Report assembly (M8.15/M8.16): deterministic backend facts only.

The report contains sections 1-9 from the M8 spec; every value is read from
the M1/M4/M5/M6/M7 subsystems — no LLM-generated factual fields. Provenance
is carried verbatim so the report is understandable without the application.
No secrets or environment variables are ever included (D-045).
"""

from __future__ import annotations

import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT_SECTIONS = [
    "document_information",
    "architecture_summary",
    "entities",
    "relationships",
    "findings",
    "revision_changes",
    "potential_impacts",
    "evidence_provenance",
    "limitations",
]

_LIMITATIONS = [
    "Synthetic corpus: statistics and findings describe the generated ABC HLD "
    "documents, not a production AUTOSAR architecture.",
    "Findings are deterministic rules over the structured registry; they are "
    "not a safety analysis and severities are rule tiers, not risk scores.",
    "Confidence values are rule-based extraction tiers, not calibrated "
    "probabilities.",
    "Revision impact lists entities *potentially* affected by graph "
    "neighborhood; it does not claim functional breakage.",
    "Copilot answers are grounded in retrieved chunks with mechanical "
    "citation validation; questions outside the corpus are refused.",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def build_report(version: str | None = None,
                 base_version: str | None = None,
                 target_version: str | None = None,
                 include: dict | None = None,
                 session=None) -> dict:
    """Assemble the report dict from backend subsystems (deterministic)."""
    from app.services import app_services as svc

    include = include or {}
    report: dict = {
        "report": "ARCHSENSE",
        "generated_at": _now_iso(),
        "selections": {
            "version": version,
            "base_version": base_version,
            "target_version": target_version,
            "include": include,
        },
    }

    # 1. document information -------------------------------------------
    doc_info: dict = {"versions_available": svc.get_versions(session=session)}
    if version:
        summary = svc.get_document_summary(version, session=session)
        doc_info["selected"] = summary
    report["document_information"] = doc_info

    # 2-4. architecture ---------------------------------------------------
    arch: dict = {}
    entities: list[dict] = []
    relationships: list[dict] = []
    if version and include.get("architecture", True):
        try:
            stats = svc.get_architecture_stats(version)
            arch = stats or {}
        except svc.ServiceError as exc:
            arch = {"unavailable": str(exc)}
        try:
            graph, _stats = svc.get_architecture_graph(version)
            entities = [
                {"entity_key": node, **{k: v for k, v in data.items()
                                        if k != "provenance"}}
                for node, data in sorted(graph.nodes(data=True))
            ]
            for frm, to, key, data in sorted(
                    graph.edges(keys=True, data=True),
                    key=lambda e: (str(e[0]), str(e[2]))):
                relationships.append({
                    "subject": frm, "predicate": data.get("predicate"),
                    "object": to, "fact_key": key,
                    "confidence": data.get("confidence"),
                    "extractor": data.get("extractor"),
                    "provenance": data.get("provenance", {}),
                })
        except svc.ServiceError as exc:
            arch["graph_unavailable"] = str(exc)
    report["architecture_summary"] = arch
    report["entities"] = entities
    report["relationships"] = relationships

    # 5. findings ---------------------------------------------------------
    findings: list[dict] = []
    if version and include.get("findings", True):
        try:
            findings = svc.analyze_findings(version)["findings"]
        except svc.ServiceError as exc:
            findings = [{"error": str(exc)}]
    report["findings"] = findings

    # 6-7. revision changes + impacts --------------------------------------
    changes: list[dict] = []
    impacts: list[dict] = []
    summary: dict = {}
    if base_version and target_version and include.get("comparison", True):
        try:
            comp = svc.compare_revisions(base_version, target_version)
            summary = comp.get("summary", {})
            changes = comp.get("entity_changes", []) + comp.get(
                "relationship_changes", [])
            impacts = comp.get("impacts", [])
        except svc.ServiceError as exc:
            summary = {"error": str(exc)}
    report["revision_changes"] = changes
    report["potential_impacts"] = impacts
    report["revision_summary"] = summary

    # 8. evidence / provenance ----------------------------------------------
    evidence: list[dict] = []
    if version and include.get("evidence", True):
        try:
            graph, _stats = svc.get_architecture_graph(version)
            seen: set[str] = set()
            for _frm, _to, key, data in graph.edges(keys=True, data=True):
                prov = data.get("provenance", {}) or {}
                ck = str(prov.get("source_chunk_id", key))
                if ck in seen:
                    continue
                seen.add(ck)
                evidence.append({"fact_key": key, **prov})
        except svc.ServiceError:
            pass
    report["evidence_provenance"] = evidence

    # 9. limitations ---------------------------------------------------------
    report["limitations"] = list(_LIMITATIONS)
    return report


def report_to_json(report: dict) -> str:
    """Deterministic JSON serialization of a report."""
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=False)


def report_findings_to_csv(report: dict) -> str:
    """Findings section as CSV (empty string when no findings)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["finding_type", "severity", "title", "status",
                     "confidence", "entity_keys", "description"])
    for f in report.get("findings", []):
        if "error" in f:
            continue
        writer.writerow([
            f.get("finding_type", ""), f.get("severity", ""),
            f.get("title", ""), f.get("status", ""),
            f.get("confidence", ""),
            ";".join(f.get("entity_keys", []) or []),
            (f.get("description", "") or "").replace("\n", " "),
        ])
    return buf.getvalue()


def _prov_lines(prov) -> str:
    """Render an M7 provenance snapshot list into one CSV cell."""
    if isinstance(prov, dict):
        prov = [prov]
    parts = []
    for p in prov or []:
        bits = [str(p.get("document_name", "")),
                f"v{p.get('version_label', '')}",
                f"Section {p.get('section_no', '')}",
                f"pp.{p.get('page_start', '?')}-{p.get('page_end', '?')}"]
        chunk = str(p.get("source_chunk_id", "") or "")
        if chunk:
            bits.append(f"chunk {chunk[:8]}")
        parts.append(" ".join(b for b in bits if b.strip("v. ?")))
    return " | ".join(parts)


def _path_str(path) -> str:
    """Render an M7 PathStep list into one readable CSV cell."""
    out = []
    for step in path or []:
        if not isinstance(step, dict):
            out.append(str(step))
            continue
        if step.get("direction", "forward") == "reverse":
            out.append(f"{step.get('frm')} <--{step.get('predicate')}-- "
                       f"{step.get('to')}")
        else:
            out.append(f"{step.get('frm')} --{step.get('predicate')}--> "
                       f"{step.get('to')}")
    return "  ->  ".join(out)


def report_changes_to_csv(report: dict) -> str:
    """Revision changes section as CSV (empty when no comparison)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["change_type", "entity_or_subject", "predicate",
                     "object", "provenance"])
    for c in report.get("revision_changes", []):
        writer.writerow([
            c.get("change_type", ""),
            c.get("entity_key") or c.get("subject", ""),
            c.get("predicate", ""),
            c.get("object", ""),
            _prov_lines(c.get("provenance")),
        ])
    return buf.getvalue()


def report_impacts_to_csv(report: dict) -> str:
    """Potential impacts section as CSV."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["source_change_id", "impacted_entity_key", "category",
                     "depth", "reason", "path"])
    for i in report.get("potential_impacts", []):
        writer.writerow([
            i.get("source_change_id", ""), i.get("impacted_entity_key", ""),
            i.get("category", ""), i.get("depth", ""),
            i.get("reason", ""), _path_str(i.get("path")),
        ])
    return buf.getvalue()
