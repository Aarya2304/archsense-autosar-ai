"""Extraction evaluation (M4.17/M4.18) against the M0 ground truth.

Gold entities and facts are derived mechanically from the ground-truth
registries (the same source-of-truth model that rendered the PDFs), so
extraction quality is measured against exact, page-resolved truth:

  gold facts
    provides         interfaces[i].provider -> IF.id
    requires         interfaces[i].consumers -> IF.id
    depends_on       dependencies source -> target (all labels unified)
    carries          signals[i].interface_id -> SG.id
    implements       ports[i] (P.id: component_id, interface_id, direction)
    participates_in  flows[i].steps -> FL.id

Evaluation keys are canonical ID keys ("component:C-10"); duplicate cases
are evaluated separately (same fact extracted from multiple chunks must
dedupe to one registry fact).
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from backend.extraction.models import ExtractedEntity, ExtractedFact
from backend.extraction.service import ExtractionService
from backend.rag.models import Chunk


# ------------------------------------------------------------------ gold ----

def _idkey(etype: str, raw: str) -> str:
    return f"{etype}:{str(raw).strip().upper()}"


def gold_entities(gt_version: dict, ports: list[dict] | None = None) -> set[str]:
    """Canonical gold entity keys for one version's registry."""
    keys = set()
    for c in gt_version["components"]:
        keys.add(_idkey("component", c["id"]))
    for i in gt_version["interfaces"]:
        keys.add(_idkey("interface", i["id"]))
    for s in gt_version["signals"]:
        keys.add(_idkey("signal", s["id"]))
    for d in gt_version["dependencies"]:
        keys.add(_idkey("dependency", d["id"]))
    for f in gt_version["flows"]:
        keys.add(_idkey("functional_flow", f["id"]))
    for p in ports or []:
        keys.add(_idkey("port", p["id"]))
    return keys


def gold_facts(gt_version: dict, ports: list[dict]) -> set[tuple[str, str, str]]:
    """Canonical gold (subject, predicate, object) triples for one version."""
    facts = set()
    for i in gt_version["interfaces"]:
        iid = _idkey("interface", i["id"])
        facts.add((_idkey("component", i["provider"]), "provides", iid))
        for c in i["consumers"]:
            facts.add((_idkey("component", c), "requires", iid))
    for s in gt_version["signals"]:
        facts.add((_idkey("interface", s["interface_id"]), "carries",
                   _idkey("signal", s["id"])))
    for d in gt_version["dependencies"]:
        facts.add((_idkey("component", d["source_id"]), "depends_on",
                   _idkey("component", d["target_id"])))
    for p in ports:
        facts.add((_idkey("port", p["id"]), "implements",
                   _idkey("interface", p["interface_id"])))
    for fl in gt_version["flows"]:
        fid = _idkey("functional_flow", fl["id"])
        for step in fl["steps"]:
            facts.add((_idkey("component", step), "participates_in", fid))
    return facts


# ---------------------------------------------------------------- scoring ----

def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4),
            "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn}


@dataclass
class EvalResult:
    version: str
    entities: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    facts_by_predicate: dict = field(default_factory=dict)
    provenance_accuracy: float = 0.0
    duplicate_facts_merged: int = 0
    validation_issues: int = 0
    timings_ms: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "entities": self.entities,
            "facts": self.facts,
            "facts_by_predicate": self.facts_by_predicate,
            "provenance_accuracy": self.provenance_accuracy,
            "duplicate_facts_merged": self.duplicate_facts_merged,
            "validation_issues": self.validation_issues,
            "timings_ms": self.timings_ms,
        }


def _score_provenance(result, all_chunks: Sequence[Chunk]) -> float:
    """Share of facts whose evidence chunk_id exists in the version's chunks."""
    valid_ids = {c.chunk_id for c in all_chunks}
    ok = total = 0
    for f in result.facts:
        src = f.evidence.source
        if src is None:
            continue
        total += 1
        if src.chunk_id in valid_ids:
            ok += 1
    return round(ok / total, 4) if total else 0.0


def evaluate_extraction(version: str, chunks: Sequence[Chunk],
                        gt_version: dict, gt_ports: list[dict],
                        min_confidence: float = 0.0,
                        use_llm: bool = False,
                        llm_provider=None) -> EvalResult:
    """Run the extractor on one version and score it against gold (M4.18)."""
    t0 = time.perf_counter()
    svc = ExtractionService(llm_provider=llm_provider, use_llm=use_llm,
                            min_confidence=min_confidence)
    result = svc.extract_from_chunks(chunks, document_name=f"eval_{version}",
                                     version=version, persist=False)
    total_ms = (time.perf_counter() - t0) * 1000.0

    g_ent = gold_entities(gt_version, gt_ports)
    g_fact = gold_facts(gt_version, gt_ports)

    pred_ent = {e.key for e in result.entities}
    pred_fact = {(f.subject, f.predicate.value, f.object)
                 for f in result.facts}

    ent_scores = _prf(len(g_ent & pred_ent),
                      len(pred_ent - g_ent), len(g_ent - pred_ent))

    # per-predicate fact scores (macro view) + micro over all facts
    by_pred: dict[str, dict] = {}
    micro_tp = micro_fp = micro_fn = 0
    for pred in ("provides", "requires", "depends_on", "carries",
                 "implements", "participates_in"):
        g = {t for t in g_fact if t[1] == pred}
        p = {t for t in pred_fact if t[1] == pred}
        tp, fp, fn = len(g & p), len(p - g), len(g - p)
        micro_tp += tp
        micro_fp += fp
        micro_fn += fn
        by_pred[pred] = _prf(tp, fp, fn)

    # duplicates merged: reported by the validator's dedupe stats
    duplicate_facts_merged = result.stats.get("validated",
                                              {}).get("duplicate_facts", 0)

    prov = _score_provenance(result, chunks)

    return EvalResult(
        version=version,
        entities=ent_scores,
        facts=_prf(micro_tp, micro_fp, micro_fn),
        facts_by_predicate=by_pred,
        provenance_accuracy=prov,
        duplicate_facts_merged=duplicate_facts_merged,
        validation_issues=len(result.issues),
        timings_ms={"total_ms": round(total_ms, 1),
                    **result.timings_ms},
    )


def run_evaluation(processed: dict[str, Path], ground_truth: dict,
                   min_confidence: float = 0.0) -> dict:
    """Full two-version evaluation; returns the machine-readable report."""
    from backend.rag.chunker import chunk_processed_json

    report: dict = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "min_confidence": min_confidence, "versions": {}}
    for ver, pj in processed.items():
        chunks = chunk_processed_json(pj)
        gt_v = ground_truth["entities"]["v1" if ver == "1.0.0" else "v2"]
        ports = ground_truth["ports"]["v1" if ver == "1.0.0" else "v2"]
        res = evaluate_extraction(ver, chunks, gt_v, ports,
                                  min_confidence=min_confidence)
        report["versions"][ver] = res.to_dict()

        # per-example detail for the two smallest predicate families
        detail = {}
        svc = ExtractionService(min_confidence=min_confidence)
        r = svc.extract_from_chunks(chunks, document_name=f"eval_{ver}",
                                    version=ver, persist=False)
        pred_fact = {(f.subject, f.predicate.value, f.object)
                     for f in r.facts}
        gt_v_facts = gold_facts(gt_v, ports)
        for pred in ("depends_on", "participates_in"):
            g = {t for t in gt_v_facts if t[1] == pred}
            p = {t for t in pred_fact if t[1] == pred}
            detail[pred] = {
                "missed": sorted(g - p)[:10],
                "spurious": sorted(p - g)[:10],
            }
        report["versions"][ver]["per_example_detail"] = detail
    return report


def save_report(report: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path
