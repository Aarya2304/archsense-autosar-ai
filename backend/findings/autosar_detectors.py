"""M9 AUTOSAR finding detectors — conservative, evidence-backed only.

Two detectors meaningful for the AUTOSAR Adaptive Platform vocabulary; the
ABC suite (undefined_reference, dangling_requires, ...) does NOT apply to
this profile and is not run against it (task Part M: never manufacture
defects). Zero findings is a valid, honest result.

INCONSISTENT_CLASSIFICATION rule
    The R20-11 document is explicit: Functional Clusters *belong to* either
    Adaptive Platform Foundation OR Adaptive Platform Services (p15, p18),
    and Foundation FCs are Library-based while Services are Service-based.
    A functional_cluster carrying belongs_to facts to BOTH parents in the
    same version contradicts that classification model.

UNDEFINED_REFERENCE note
    The generic undefined_reference concept (a fact whose endpoint is not a
    registered entity) is re-instantiated here over the autosar:* key space
    with the same mechanics as the ABC detector, but reads the
    profile-generic registry instead of the typed ABC tables.

Both detectors emit findings whose provenance is copied verbatim from the
trusted registry rows (D-022/D-028); confidence is the max of supporting
fact confidences (same documented aggregation rule as M6).
"""

from __future__ import annotations

from collections import defaultdict

from backend.findings.context import (AnalysisContext, canonical_key,
                                      provenance_of_entity,
                                      provenance_of_fact)
from backend.findings.detectors import _mk
from backend.findings.models import EvidenceItem, Finding, FindingType
from backend.storage.models import FindingSeverity

__all__ = ["detect_autosar_undefined_references",
           "detect_inconsistent_classification",
           "AUTOSAR_DETECTORS", "AUTOSAR_FINDING_TYPES"]

# FindingType stays the M6 controlled vocabulary; the AUTOSAR-specific
# inconsistent-classification rule maps onto UNDEFINED_REFERENCE's
# "violates the structured model" semantics via its own detector name and
# description (no new enum value -> M1 schema untouched, D-048).
AUTOSAR_FINDING_TYPES = {
    "undefined_reference": FindingType.UNDEFINED_REFERENCE,
    "inconsistent_classification": FindingType.UNDEFINED_REFERENCE,
}


def detect_autosar_undefined_references(ctx: AnalysisContext) -> list[Finding]:
    """A fact whose subject/object has no entity row in this version."""
    out: list[Finding] = []
    for fk in sorted(ctx.facts):
        f = ctx.facts[fk]
        if not str(f.subject).startswith("autosar:"):
            continue                     # ABC facts handled by the ABC suite
        missing = [side for side in (f.subject, f.object)
                   if side not in ctx.entities]
        if not missing:
            continue
        out.append(_mk(
            FindingType.UNDEFINED_REFERENCE, ctx, FindingSeverity.HIGH,
            entity_keys=missing, fact_keys=[fk],
            title=f"Fact references undefined entity {missing[0]}",
            description=(
                f"Fact {fk!r} references {', '.join(missing)} which has no "
                f"row in the trusted entity registry for v"
                f"{ctx.version_label}. The fact is reported, not silently "
                f"discarded."),
            detector="AutosarUndefinedReferenceDetector",
            confidences=[f.confidence],
            extra_evidence=[EvidenceItem(kind="entity", key=m, note="missing")
                            for m in missing],
            metadata={"missing_keys": sorted(missing),
                      "profile": "autosar_adaptive_platform"}))
    return out


def _partition_parents(ctx: AnalysisContext) -> tuple[set[str], set[str]]:
    """Resolve the Foundation / Services parent keys from the registry.

    The partition parents are discovered from the entity registry (any key
    in the ``autosar:platform_foundation`` / ``autosar:platform_service``
    class), not hardcoded — the document's surface forms are name-addressed
    and the registry may legitimately hold plural concept entities.
    """
    apf = {k for k in ctx.entities
           if k.startswith("autosar:platform_foundation:")}
    aps = {k for k in ctx.entities
           if k.startswith("autosar:platform_service:")}
    return apf, aps


def detect_inconsistent_classification(ctx: AnalysisContext) -> list[Finding]:
    """A functional_cluster classified into BOTH platform partitions.

    Rule: belongs_to facts from one FC to a Foundation parent AND a Services
    parent in the same version contradict the document's either/or
    classification model ("Functional Clusters ... belong to either Adaptive
    Platform Foundation or Adaptive Platform Services", p15). The extractor
    can legitimately emit both when prose is ambiguous about a specific FC;
    that inconsistency is exactly what this detector surfaces. Zero findings
    is a valid, honest result for a clean registry.
    """
    APF, APS = _partition_parents(ctx)

    belongs: dict[str, dict[str, list]] = defaultdict(
        lambda: {"apf": [], "aps": []})
    for f in ctx.facts.values():
        if f.predicate != "belongs_to":
            continue
        subj = canonical_key(f.subject)
        obj = canonical_key(f.object)
        if not subj.startswith("autosar:functional_cluster:"):
            continue
        if obj in APF:
            belongs[subj]["apf"].append(f)
        elif obj in APS:
            belongs[subj]["aps"].append(f)

    out: list[Finding] = []
    for fc in sorted(belongs):
        sides = belongs[fc]
        if not (sides["apf"] and sides["aps"]):
            continue
        fks = [f.fact_key for f in sides["apf"] + sides["aps"]]
        confs = [f.confidence for f in sides["apf"] + sides["aps"]]
        out.append(_mk(
            FindingType.UNDEFINED_REFERENCE, ctx, FindingSeverity.MEDIUM,
            entity_keys=[fc] + sorted(APF) + sorted(APS),
            fact_keys=sorted(fks),
            title=f"{fc} classified into both platform partitions",
            description=(
                f"Functional Cluster {fc} carries belongs_to facts to BOTH "
                f"Adaptive Platform Foundation and Adaptive Platform "
                f"Services in v{ctx.version_label}. The document's "
                f"classification model assigns each Functional Cluster to "
                f"exactly one partition (Foundation = Library-based, "
                f"Services = Service-based), so this is an inconsistent "
                f"classification in the structured registry."),
            detector="InconsistentClassificationDetector",
            confidences=confs,
            metadata={"partition_facts": {"foundation": sorted(
                f.fact_key for f in sides["apf"]),
                "services": sorted(f.fact_key for f in sides["aps"])},
                "profile": "autosar_adaptive_platform"}))
    return out


AUTOSAR_DETECTORS: dict[str, object] = {
    "undefined_reference": detect_autosar_undefined_references,
    "inconsistent_classification": detect_inconsistent_classification,
}
