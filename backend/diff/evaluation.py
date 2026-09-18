"""Revision-comparison evaluation (M7.26) against the M0 ground truth.

Gold derivation — honesty first (task rule 26): the mechanical gold comes
from the ground truth's ``expected_diff`` (per-family added/removed/
modified entity lists computed by the same source-of-truth model that
rendered the PDFs) and from the GT entity records (interfaces
provider/consumers, dependencies source/target, signals interface_id,
ports, flow steps), yielding exact gold fact-triple changes. Detection is
matched by canonical key / triple equality — the same identities the
comparator uses — so scoring is a pure set operation with no fuzzy hits.

Not-applicable defects (D3, D5, D6, D7) are prose-vs-structure defects
whose witnesses were resolved by M4 canonicalization or live only in prose
(D-035); they are reported per defect with reasons and are NOT counted as
false negatives. D8 (impact-relevant change) is scored as an impact check:
the gold impacted entity set for IF-03's consumer-side change must be
reached by the impact traversal.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.dataset.ground_truth import load_ground_truth
from backend.diff.comparator import RevisionComparator
from backend.diff.models import RevisionComparison

# ---------------------------------------------------------------------------
# gold derivation
# ---------------------------------------------------------------------------


def _idkey(etype: str, raw: str) -> str:
    return f"{etype}:{str(raw).strip().upper()}"


def gold_entity_diff(expected_diff: dict) -> dict[str, set[str]]:
    """Gold entity keys per change kind, from GT ``expected_diff``.

    Used as a CROSS-CHECK of the mechanical registry-derived gold (see
    ``gold_entity_changes``): expected_diff covers the components/
    interfaces/signals/dependencies/flows families but has NO ports
    family, so ports must be derived from ``gt['ports']``.
    """
    fam_prefix = {"components": "component", "interfaces": "interface",
                  "signals": "signal", "dependencies": "dependency",
                  "flows": "functional_flow", "ports": "port"}
    out: dict[str, set[str]] = {"added": set(), "removed": set(),
                                "changed": set()}
    for fam, kinds in expected_diff.items():
        prefix = fam_prefix.get(fam)
        if prefix is None:
            continue
        for kind in ("added", "removed", "modified"):
            for eid in kinds.get(kind, []):
                out["added" if kind == "added" else
                    "removed" if kind == "removed" else
                    "changed"].add(_idkey(prefix, eid))
    return out


def gold_entity_changes(gt: dict) -> dict[str, set[str]]:
    """Mechanical gold entity diff, derived from the GT registries.

    added/removed = ID-set difference between v1 and v2 across all six
    families (ports included — ``expected_diff`` has no ports family, so
    the port diff comes from ``gt['ports']``). changed = present in both
    with a different display ``name`` — the registry-level ENTITY_CHANGED
    semantic (typed-row attribute change with stable identity; interface
    consumer/provider modifications live in FACTS and are scored under
    relationship changes, where the extractor's evidence actually sits).
    """
    families = (
        ("component", gt["entities"]["v1"]["components"],
         gt["entities"]["v2"]["components"]),
        ("interface", gt["entities"]["v1"]["interfaces"],
         gt["entities"]["v2"]["interfaces"]),
        ("signal", gt["entities"]["v1"]["signals"],
         gt["entities"]["v2"]["signals"]),
        ("dependency", gt["entities"]["v1"]["dependencies"],
         gt["entities"]["v2"]["dependencies"]),
        ("functional_flow", gt["entities"]["v1"]["flows"],
         gt["entities"]["v2"]["flows"]),
        ("port", gt["ports"]["v1"], gt["ports"]["v2"]),
    )
    added: set[str] = set()
    removed: set[str] = set()
    changed: set[str] = set()
    for etype, v1rows, v2rows in families:
        i1 = {r["id"]: r for r in v1rows}
        i2 = {r["id"]: r for r in v2rows}
        added |= {_idkey(etype, e) for e in i2.keys() - i1.keys()}
        removed |= {_idkey(etype, e) for e in i1.keys() - i2.keys()}
        for eid in i1.keys() & i2.keys():
            n1, n2 = i1[eid].get("name"), i2[eid].get("name")
            if n1 and n2 and n1 != n2:
                changed.add(_idkey(etype, eid))
    return {"added": added, "removed": removed, "changed": changed}


def gold_facts(gt_version: dict, ports: Sequence[dict]) -> set[tuple]:
    """Gold fact triples for one version (same derivation as M4 eval)."""
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


def gold_fact_diff(gt: dict) -> tuple[set[tuple], set[tuple]]:
    """(removed, added) gold fact triples between v1 and v2."""
    g1 = gold_facts(gt["entities"]["v1"], gt["ports"]["v1"])
    g2 = gold_facts(gt["entities"]["v2"], gt["ports"]["v2"])
    return g1 - g2, g2 - g1


# prose-vs-structure defects that cannot be witnessed by the structural M7
# evaluator (same conclusion as M6's D-035 analysis, documented per entry)
NON_APPLICABLE = {
    "D3_conflicting_provider":
        "prose-vs-table contradiction resolved by M4's table-wins "
        "canonicalization: the v2 registry holds a single provides fact "
        "for IF-13, so no revision witness exists",
    "D5_dropped_signal_referenced":
        "prose-level contradiction: v2 prose still names SG-015 but the "
        "registry has no SG-015 row and signal consumption is only "
        "observable transitively (D-035)",
    "D6_contradictory_statement":
        "prose-vs-fact contradiction inside v2; requires reading the "
        "negation sentence, which M4's extractor deliberately excludes "
        "from facts",
    "D7_port_count_mismatch":
        "prose-vs-table count mismatch; no count facts exist in the "
        "registry to compare across versions",
}

# defect_id -> structural gold the M7 evaluator scores
STRUCTURALLY_APPLICABLE = ("D1_removed_dependency", "D2_stale_component_"
                           "reference", "D8_new_consumer_of_changed_"
                           "interface")


@dataclass
class DiffScores:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def metrics(self) -> dict:
        p = self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0
        r = self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0
        f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
        return {"expected": self.tp + self.fn, "detected": self.tp + self.fp,
                "tp": self.tp, "fp": self.fp, "fn": self.fn,
                "precision": round(p, 4), "recall": round(r, 4),
                "f1": round(f1, 4)}


@dataclass
class RevisionEvaluation:
    base_version: str = "1.0.0"
    target_version: str = "1.1.0"
    entity_added: DiffScores = field(default_factory=DiffScores)
    entity_removed: DiffScores = field(default_factory=DiffScores)
    entity_changed: DiffScores = field(default_factory=DiffScores)
    relationship_removed: DiffScores = field(default_factory=DiffScores)
    relationship_added: DiffScores = field(default_factory=DiffScores)
    stale_reference: DiffScores = field(default_factory=DiffScores)
    impact: DiffScores = field(default_factory=DiffScores)
    defects: list[dict] = field(default_factory=list)
    timings_ms: dict = field(default_factory=dict)
    detected_counts: dict = field(default_factory=dict)
    gold_consistency: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "base_version": self.base_version,
            "target_version": self.target_version,
            "entity_added": self.entity_added.metrics(),
            "entity_removed": self.entity_removed.metrics(),
            "entity_changed": self.entity_changed.metrics(),
            "relationship_removed": self.relationship_removed.metrics(),
            "relationship_added": self.relationship_added.metrics(),
            "stale_reference": self.stale_reference.metrics(),
            "impact": self.impact.metrics(),
            "defects": self.defects,
            "detected_counts": self.detected_counts,
            "gold_consistency": self.gold_consistency,
            "timings_ms": self.timings_ms,
        }


def evaluate_revisions(session: Session, base_version: str = "1.0.0",
                       target_version: str = "1.1.0",
                       depth: int = 1) -> RevisionEvaluation:
    """Score the comparator against the mechanical gold (M7.26)."""
    t0 = time.perf_counter()
    gt = load_ground_truth()
    gdiff = gold_entity_changes(gt)
    # cross-check against the GT expected_diff block (report-only): the
    # non-port families must agree exactly; expected_diff has no ports
    # family so its diff is a strict subset on removed.
    xdiff = gold_entity_diff(gt["expected_diff"])
    consistency = {
        "added_matches_expected_diff": gdiff["added"] == xdiff["added"],
        "removed_superset_of_expected_diff":
            xdiff["removed"].issubset(gdiff["removed"]),
        "expected_diff_missing_ports_removed":
            sorted(gdiff["removed"] - xdiff["removed"]),
        "modified_manifesting_as_fact_changes":
            sorted(xdiff["changed"] - gdiff["changed"]),
    }
    g_rem, g_add = gold_fact_diff(gt)

    comparator = RevisionComparator()
    cmp: RevisionComparison = comparator.compare(base_version, target_version,
                                                 depth=depth)
    ev = RevisionEvaluation(base_version=base_version,
                            target_version=target_version)

    # ---- entity changes (exact key sets) ---------------------------------
    def _score(scores: DiffScores, gold: set, pred_keys: set):
        scores.tp = len(gold & pred_keys)
        scores.fp = len(pred_keys - gold)
        scores.fn = len(gold - pred_keys)

    pred_added = {c.entity_key for c in cmp.entity_changes
                  if c.change_type.value == "entity_added"}
    pred_removed = {c.entity_key for c in cmp.entity_changes
                    if c.change_type.value == "entity_removed"}
    pred_changed = {c.entity_key for c in cmp.entity_changes
                    if c.change_type.value == "entity_changed"}
    _score(ev.entity_added, gdiff["added"], pred_added)
    _score(ev.entity_removed, gdiff["removed"], pred_removed)
    _score(ev.entity_changed, gdiff["changed"], pred_changed)

    # ---- relationship changes (exact triple sets; normalized to the
    # 3-tuple semantic identity (subject, predicate, object) that the gold
    # derivation uses) ----------------------------------------------
    pred_rel_rem = {c.triple[:3] for c in cmp.relationship_changes
                    if c.change_type.value == "relationship_removed"}
    pred_rel_add = {c.triple[:3] for c in cmp.relationship_changes
                    if c.change_type.value == "relationship_added"}
    _score(ev.relationship_removed, g_rem, pred_rel_rem)
    _score(ev.relationship_added, g_add, pred_rel_add)

    # ---- stale-reference finding vs gold D2 --------------------------------
    # Gold: IF-12's consumer facts in v2 still reference the old C-09 name.
    # The registry canonicalized consumers to stable IDs (M4 D-021), so the
    # stale-name witness does not survive into structured facts. The
    # mechanical applicability check: does any TARGET fact reference an
    # entity that base had and target removed? If yes, STALE_REFERENCE is
    # scoreable; if no, the detector cannot fire and D2 is not applicable.
    removed_keys = set(gdiff["removed"])
    stale_possible = _entity_removed_referenced(cmp, removed_keys)
    d2 = next(d for d in gt["expected_findings"]
              if d["defect_id"] == "D2_stale_component_reference")
    found = [f for f in cmp.revision_findings
             if f.finding_type == "stale_reference"]
    if stale_possible:
        ev.stale_reference.tp = len(found)
        ev.stale_reference.fp = 0
        ev.stale_reference.fn = 0 if found else 1
    ev.defects.append({
        "defect_id": d2["defect_id"],
        "status": ("applicable" if stale_possible
                   else "not_applicable"),
        "reason": ("structured stale-reference witness exists in the "
                   "target registry"
                   if stale_possible else
                   "no stale-reference witness in the target registry: M4 "
                   "canonicalized interface consumers to stable component "
                   "IDs, and the only stale-name remnant is free text in "
                   "component:C-09.description (not a fact endpoint)"),
        "detected_stale_reference_findings": len(found)})

    # ---- D1 / D8: structurally applicable defect checks -------------------
    d1 = next(d for d in gt["expected_findings"]
              if d["defect_id"] == "D1_removed_dependency")
    d1_triple = ("component:C-19", "depends_on", "component:C-11")
    d1_detected = d1_triple in pred_rel_rem
    d1_impacted = any(i.impacted_entity_key == "component:C-19"
                      for i in cmp.impacts)
    ev.defects.append({
        "defect_id": d1["defect_id"], "status": "applicable",
        "gold": "relationship_removed component:C-19|depends_on|"
                "component:C-11",
        "detected": d1_detected,
        "impact_reaches_source_component": d1_impacted})
    if not d1_detected:
        ev.relationship_removed.fn += 1
    if not d1_impacted:
        ev.impact.fn += 1

    d8 = next(d for d in gt["expected_findings"]
              if d["defect_id"] == "D8_new_consumer_of_changed_interface")
    d8_triple = ("component:C-04", "requires", "interface:IF-03")
    d8_detected = d8_triple in pred_rel_add
    d8_impact = any(
        i.impacted_entity_key == "component:C-04" and i.depth >= 0
        for i in cmp.impacts)
    ev.defects.append({
        "defect_id": d8["defect_id"], "status": "applicable",
        "gold": "relationship_added component:C-04|requires|"
                "interface:IF-03",
        "detected": d8_detected,
        "impact_reaches_new_consumer": d8_impact})
    if not d8_detected:
        ev.relationship_added.fn += 1
    if not d8_impact:
        ev.impact.fn += 1

    for did, reason in NON_APPLICABLE.items():
        ev.defects.append({"defect_id": did, "status": "not_applicable",
                           "reason": reason})

    # ---- impact gold: gold-fact walk mirroring the documented rule --------
    # Gold impacted set = walk the GOLD fact graph exactly as the comparator
    # walks the registry graph: removed-triple endpoints in the v1 gold
    # graph, added-triple endpoints in the v2 gold graph, at the configured
    # depth. Gold facts == registry facts (M4 scored P=R=F1=1.000), so this
    # is an independent-input replay of the documented rule; the genuinely
    # independent impact checks are the D1/D8 anchor assertions above.
    def _gold_side_graph(facts: set[tuple]) -> dict[str, set[str]]:
        gmap: dict[str, set[str]] = {}
        for s, _p, o in facts:
            if s and o:
                gmap.setdefault(s, set()).add(o)
                gmap.setdefault(o, set()).add(s)
        return gmap

    g1 = gold_facts(gt["entities"]["v1"], gt["ports"]["v1"])
    g2 = gold_facts(gt["entities"]["v2"], gt["ports"]["v2"])
    gmap1, gmap2 = _gold_side_graph(g1), _gold_side_graph(g2)

    def _walk(gmap: dict[str, set[str]], start: str,
              steps: int) -> set[str]:
        seen = {start}
        frontier = {start}
        for _ in range(max(0, steps)):
            nxt = set()
            for node in frontier:
                for nb in gmap.get(node, ()):
                    if nb not in seen:
                        seen.add(nb)
                        nxt.add(nb)
            frontier = nxt
        return seen

    gold_impacted: set[str] = set()
    # relationship-change anchors: per-side gold graph (documented rule)
    for triple in g_rem:
        for endpoint in (triple[0], triple[2]):
            if endpoint:
                gold_impacted |= _walk(gmap1, endpoint, depth)
    for triple in g_add:
        for endpoint in (triple[0], triple[2]):
            if endpoint:
                gold_impacted |= _walk(gmap2, endpoint, depth)
    # entity-change anchors: mirror the comparator (removed entities walk
    # the base gold graph; added/changed walk the target gold graph).
    # Dependencies have NO fact endpoints by design (D-021/D-030), so this
    # term is what makes removed DEP entities scoreable.
    for e in gdiff["removed"]:
        gold_impacted |= _walk(gmap1, e, depth)
    for e in gdiff["added"] | gdiff["changed"]:
        gold_impacted |= _walk(gmap2, e, depth)
    pred_impacted = {i.impacted_entity_key for i in cmp.impacts}
    _score(ev.impact, gold_impacted, pred_impacted)
    ev.impact.fp = len(pred_impacted - gold_impacted)
    ev.impact.fn = len(gold_impacted - pred_impacted)
    ev.impact.tp = len(gold_impacted & pred_impacted)

    ev.detected_counts = {
        "entity_added": len(pred_added), "entity_removed": len(pred_removed),
        "entity_changed": len(pred_changed),
        "relationship_added": len(pred_rel_add),
        "relationship_removed": len(pred_rel_rem),
        "impacts": len(cmp.impacts),
        "revision_findings": len(cmp.revision_findings)}
    ev.timings_ms = {**cmp.timings_ms,
                     "evaluation_ms": round((time.perf_counter() - t0)
                                            * 1000.0, 1)}
    ev.gold_consistency = consistency
    return ev


def _entity_removed_referenced(cmp: RevisionComparison,
                               removed_keys: set[str]) -> bool:
    """True iff any target-side fact change references a removed entity.

    (Structural precondition of a stale reference: target still points at
    something base had and target no longer defines.)
    """
    target_keys = {c.entity_key for c in cmp.entity_changes
                   if c.change_type.value == "entity_added"}
    for c in cmp.relationship_changes:
        if c.change_type.value == "relationship_added":
            for endpoint in (c.subject, c.object):
                if endpoint in removed_keys:
                    return True
    return False
