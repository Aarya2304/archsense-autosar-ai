"""Deterministic finding detectors (M6).

Six detector families required by the M6 task. Every detector:

- is a pure function over :class:`~backend.findings.context.AnalysisContext`
  (version-scoped, prebuilt lookups — no DB scans in loops),
- emits findings whose provenance is copied verbatim from trusted M4/M5
  registry metadata (never invented),
- defines its exact rule in the module docstring so the semantics are
  auditable, including what the current structured model CANNOT express
  (AUTOSAR semantic caution, task rule 21).

No LLM anywhere: detectors are mechanical set/graph logic.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from backend.findings.context import (AnalysisContext, canonical_key,
                                      provenance_of_entity,
                                      provenance_of_fact)
from backend.findings.models import (EvidenceItem, Finding, FindingType,
                                     deterministic_finding_id)
from backend.storage.models import FindingSeverity

__all__ = ["ALL_DETECTORS", "detect_undefined_references",
           "detect_dangling_requires", "detect_duplicate_interfaces",
           "detect_conflicting_providers", "detect_orphan_entities",
           "detect_unconsumed_signals"]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _mk(finding_type: FindingType, ctx: AnalysisContext, severity: FindingSeverity,
        entity_keys: list[str], fact_keys: list[str], title: str,
        description: str, detector: str,
        confidences: list[float], extra_evidence: list[EvidenceItem] | None = None,
        metadata: dict | None = None) -> Finding:
    """Common constructor: deterministic ID + aggregated confidence + provenance.

    Confidence aggregation rule (task rule 6, documented): the finding
    inherits the MAXIMUM confidence of its supporting fact rows; entity-only
    findings inherit the entity's own registry confidence. Max (not min/mean)
    is used deliberately: a finding stands as long as its strongest witness
    is reliable; the rule is simple, deterministic and monotone.
    """
    prov: list[dict] = []
    confs: list[float] = list(confidences)
    evidence: list[EvidenceItem] = list(extra_evidence or [])
    fk = set(fact_keys)
    for fk_ in sorted(fk):
        frow = ctx.facts.get(fk_)
        if frow is not None:
            prov.append(provenance_of_fact(frow))
    # entity provenance only for entities that exist (undefined refs cannot)
    for ek in sorted(set(entity_keys)):
        row = ctx.entities.get(ek)
        if row is not None:
            p = provenance_of_entity(row)
            # typed registry rows carry section/page; document/version come
            # from the version-scoped context (single trusted source)
            p["document_name"] = p["document_name"] or ctx.document_name
            p["version_label"] = p["version_label"] or ctx.version_label
            prov.append(p)
    for i, ev in enumerate(evidence):
        if ev.kind == "fact" and ev.key in fk and not ev.provenance:
            frow = ctx.facts.get(ev.key)
            if frow is not None:
                evidence[i] = ev.model_copy(
                    update={"provenance": provenance_of_fact(frow)})
    # every finding must carry at least one traceable evidence item (V5):
    # fact-kind items for supporting facts, entity-kind for entity keys
    if not evidence:
        for fk_ in sorted(fk):
            evidence.append(EvidenceItem(kind="fact", key=fk_,
                                         note="supporting registry fact"))
        for ek in sorted(set(entity_keys)):
            evidence.append(EvidenceItem(
                kind="entity", key=ek,
                note="entity absent from registry"
                if ek not in ctx.entities else "referenced entity"))
    if not confs:
        confs = [0.5]
    fid = deterministic_finding_id(ctx.version_label, finding_type,
                                   entity_keys, fact_keys)
    return Finding(
        finding_id=fid, version_label=ctx.version_label,
        finding_type=finding_type, severity=severity, title=title,
        description=description, confidence=round(max(confs), 4),
        entity_keys=sorted(set(entity_keys)), fact_keys=sorted(set(fact_keys)),
        evidence=evidence, provenance=prov, detector=detector,
        metadata=metadata or {})


# --------------------------------------------------------------------------
# A. UNDEFINED_REFERENCE
# --------------------------------------------------------------------------
# Rule: a fact whose subject or object canonical key has no row in the
# trusted entity registry for the SAME version. Every such fact is reported
# (not silently dropped); the missing key is recorded as entity-kind evidence
# so the finding is traceable even though the entity does not exist.

def detect_undefined_references(ctx: AnalysisContext) -> list[Finding]:
    out: list[Finding] = []
    for fk in sorted(ctx.facts):
        f = ctx.facts[fk]
        missing = []
        for side in (f.subject, f.object):
            etype = side.partition(":")[0]
            if etype in ("component", "interface", "port", "signal",
                         "dependency", "functional_flow"):
                ck = canonical_key(side)
                if ck not in ctx.entities:
                    missing.append(ck)
        if not missing:
            continue
        out.append(_mk(
            FindingType.UNDEFINED_REFERENCE, ctx, FindingSeverity.HIGH,
            entity_keys=missing, fact_keys=[fk],
            title=f"Fact references undefined entity {missing[0]}",
            description=(
                f"Fact {fk!r} references {', '.join(missing)} which has no row "
                f"in the trusted entity registry for v{ctx.version_label}. "
                f"The fact is reported, not silently discarded."),
            detector="UndefinedReferenceDetector",
            confidences=[f.confidence],
            extra_evidence=[EvidenceItem(kind="entity", key=m, note="missing")
                            for m in missing],
            metadata={"missing_keys": sorted(missing)}))
    return out


# --------------------------------------------------------------------------
# B. DANGLING_REQUIRES
# --------------------------------------------------------------------------
# Rule: component -> requires -> interface where the interface has NO
# component -> provides -> interface fact in the same version. This is an
# architecture-level problem (a needed service nobody supplies). NOTE the
# asymmetry (task rule D): an interface with consumers but no provider is
# dangling; an interface with a provider but no consumer is NOT (provision
# without demand is legitimate in this model).

def detect_dangling_requires(ctx: AnalysisContext) -> list[Finding]:
    out: list[Finding] = []
    for ifc in sorted(ctx.requires):
        if ifc in ctx.provides and ctx.provides[ifc]:
            continue
        req_facts = sorted(
            f.fact_key for f in ctx.facts.values()
            if f.predicate == "requires" and canonical_key(f.object) == ifc)
        for consumer in sorted(ctx.requires[ifc]):
            ifc_exists = ifc in ctx.entities
            out.append(_mk(
                FindingType.DANGLING_REQUIRES, ctx,
                FindingSeverity.HIGH if not ifc_exists else FindingSeverity.MEDIUM,
                entity_keys=[consumer, ifc] if ifc_exists else [consumer, ifc],
                fact_keys=req_facts,
                title=f"{consumer} requires {ifc} but nothing provides it",
                description=(
                    f"Component {consumer} requires interface {ifc}, but no "
                    f"component -> provides -> {ifc} fact exists in "
                    f"v{ctx.version_label}"
                    + ("" if ifc_exists else
                       f" and the interface entity itself is not defined")
                    + ". The dependency cannot be satisfied in this version."),
                detector="DanglingRequiresDetector",
                confidences=[ctx.facts[fk].confidence for fk in req_facts
                             if fk in ctx.facts],
                metadata={"interface_defined": ifc_exists}))
    return out


# --------------------------------------------------------------------------
# C. DUPLICATE_INTERFACE
# --------------------------------------------------------------------------
# Rule (precise, per task C): multiple DISTINCT interface entity rows in the
# same version whose canonical identity collides. M4 enforces unique
# (version_id, entity_id), so identity collision can only mean: two
# different entity_ids sharing the same normalized interface NAME — i.e. the
# document defines what canonicalizes to one interface twice. Repeated
# *references* (many requires facts) are normal and are NOT duplicates.

def detect_duplicate_interfaces(ctx: AnalysisContext) -> list[Finding]:
    byname: dict[str, list] = defaultdict(list)
    for key, row in ctx.entities.items():
        if not key.startswith("interface:"):
            continue
        norm = (getattr(row, "name", "") or "").strip().lower()
        if norm:
            byname[norm].append((key, row))
    out: list[Finding] = []
    for norm in sorted(byname):
        rows = sorted(byname[norm], key=lambda kr: kr[0])
        if len(rows) < 2:
            continue
        keys = [k for k, _ in rows]
        # supports: carries/consumers facts touching any of the duplicates
        keyset = set(keys)
        fks = sorted(f.fact_key for f in ctx.facts.values()
                     if canonical_key(f.subject) in keyset
                     or canonical_key(f.object) in keyset)
        out.append(_mk(
            FindingType.DUPLICATE_INTERFACE, ctx, FindingSeverity.MEDIUM,
            entity_keys=keys, fact_keys=fks,
            title=f"Interfaces {', '.join(k.split(':', 1)[1].upper() for k in keys)} "
                  f"share the name {norm!r}",
            description=(
                f"{len(rows)} distinct interface entities "
                f"({', '.join(keys)}) normalize to the same interface name "
                f"{norm!r} in v{ctx.version_label} — a duplicate/identity "
                f"collision under the M4 canonicalization model. Repeated "
                f"references are not duplicates; this is multiple definitions."),
            detector="DuplicateInterfaceDetector",
            confidences=[r.confidence for _, r in rows],
            metadata={"normalized_name": norm}))
    return out


# --------------------------------------------------------------------------
# D. CONFLICTING_PROVIDERS
# --------------------------------------------------------------------------
# Rule (explicit, documented MVP semantics — task D caution): two or more
# DISTINCT components carry component -> provides -> <same interface> facts
# within one version. The current structured model has NO deployment/
# service-instance dimension, so this MVP rule flags every multi-provider
# interface as a potential conflict; legitimate AUTOSAR multi-provider
# deployments cannot be distinguished from errors and this limitation is
# documented rather than hidden. (Synthetic defect D3 manifests differently:
# prose-vs-table disagreement resolved by M4's table-wins canonicalization,
# so the persisted registry contains a single provider — see evaluation.)

def detect_conflicting_providers(ctx: AnalysisContext) -> list[Finding]:
    out: list[Finding] = []
    for ifc in sorted(ctx.provides):
        providers = sorted(ctx.provides[ifc])
        if len(providers) < 2:
            continue
        prov_facts = sorted(
            f.fact_key for f in ctx.facts.values()
            if f.predicate == "provides" and canonical_key(f.object) == ifc)
        out.append(_mk(
            FindingType.CONFLICTING_PROVIDERS, ctx, FindingSeverity.HIGH,
            entity_keys=[ifc, *providers], fact_keys=prov_facts,
            title=f"Interface {ifc} claimed by {len(providers)} providers",
            description=(
                f"Components {', '.join(providers)} all carry component -> "
                f"provides -> {ifc} facts in v{ctx.version_label}. The M4/M5 "
                f"model encodes no deployment/service-instance semantics, so "
                f"this MVP rule treats multiple providers as a conflict "
                f"(documented limitation)."),
            detector="ConflictingProvidersDetector",
            confidences=[ctx.facts[fk].confidence for fk in prov_facts
                         if fk in ctx.facts],
            metadata={"providers": providers}))
    return out


# --------------------------------------------------------------------------
# E. ORPHAN_ENTITY
# --------------------------------------------------------------------------
# Rule (graph-based, per task E): a component/interface/signal/functional_flow
# node whose degree in the version graph is 0 — no edge of any predicate
# touches it. Dependency nodes are EXCLUDED: the M4 model intentionally keeps
# dependency semantics as node metadata (source_id/target_id on the typed row)
# with the canonical depends_on edge connecting the components directly, so a
# degree-0 dependency node is normal representation, not an orphan (the same
# scoping decision M5's validation made). Port nodes are also excluded: a port
# participates through port -> implements -> interface edges; a port without
# such a fact would indicate an extraction gap rather than an architecture
# problem, and is covered by UNDEFINED_REFERENCE-style fact checks instead.
# An interface with only a provider but no consumers is connected (degree > 0)
# and therefore NOT an orphan (task rule: do not call an interface dangling
# merely because it has no consumer).

def detect_orphan_entities(ctx: AnalysisContext) -> list[Finding]:
    g = ctx.graph
    out: list[Finding] = []
    ORPHAN_TYPES = ("component", "interface", "signal", "functional_flow")
    for key in sorted(n for n in g.nodes
                      if n.partition(":")[0] in ORPHAN_TYPES):
        if g.degree(key) != 0:
            continue
        row = ctx.entities.get(key)
        fks = sorted(ctx.facts_by_subject.get(key, []), key=lambda f: f.fact_key)
        fact_keys = [f.fact_key for f in fks]
        confs = ([f.confidence for f in fks]
                 + ([row.confidence] if row is not None and row.confidence else []))
        name = getattr(row, "name", key) if row is not None else key
        out.append(_mk(
            FindingType.ORPHAN_ENTITY, ctx, FindingSeverity.MEDIUM,
            entity_keys=[key], fact_keys=fact_keys,
            extra_evidence=[EvidenceItem(
                kind="entity", key=key,
                note="degree-0 node in version graph")],
            title=f"Orphan entity {key}",
            description=(
                f"Entity {key} ({name!r}) has degree 0 in the v"
                f"{ctx.version_label} architecture graph: no provides, "
                f"requires, depends_on, carries, implements or "
                f"participates_in edge touches it. Dependency nodes are "
                f"excluded by rule (their semantics live in node metadata)."),
            detector="OrphanEntityDetector",
            confidences=confs,
            metadata={"degree": 0, "entity_type": key.partition(":")[0]}))
    return out


# --------------------------------------------------------------------------
# F. UNCONSUMED_SIGNAL
# --------------------------------------------------------------------------
# Rule (strongest defensible, documented limitation — task F): a signal is
# "consumed" iff its carrying interface has at least one component ->
# requires -> <interface> fact in the same version. The M4 schema has NO
# signal-level consumption predicate (no component -> consumes -> signal),
# so consumption is established transitively through the interface. A signal
# on an interface with zero consumers is reported. Limitation: a signal on an
# interface that HAS a consumer cannot be classified as unconsumed even if
# that specific signal is never read — the model cannot see per-signal reads.

def detect_unconsumed_signals(ctx: AnalysisContext) -> list[Finding]:
    out: list[Finding] = []
    for ifc in sorted(ctx.carries):
        if ctx.requires.get(ifc):
            continue  # interface has consumers -> its signals are consumed
        signal_keys = sorted(set(ctx.carries[ifc]))
        if not signal_keys:
            continue
        # supports: carries facts for these signals
        ifkset = {ifc}
        skset = set(signal_keys)
        fks = sorted(
            f.fact_key for f in ctx.facts.values()
            if (f.predicate == "carries" and canonical_key(f.subject) in ifkset
                and canonical_key(f.object) in skset))
        for sk in signal_keys:
            exists = sk in ctx.entities
            out.append(_mk(
                FindingType.UNCONSUMED_SIGNAL, ctx, FindingSeverity.MEDIUM,
                entity_keys=[ifc, sk], fact_keys=fks,
                title=f"Signal {sk} has no consumer",
                description=(
                    f"Signal {sk} is carried by interface {ifc}, but no "
                    f"component requires {ifc} in v{ctx.version_label}; the "
                    f"M4 model has no signal-level consumption predicate, so "
                    f"consumption is inferred transitively via the interface. "
                    f"The signal therefore has no downstream consumer."),
                detector="UnconsumedSignalDetector",
                confidences=[ctx.facts[fk].confidence for fk in fks
                             if fk in ctx.facts],
                metadata={"interface": ifc, "signal_defined": exists}))
    return out


# --------------------------------------------------------------------------
# registry

ALL_DETECTORS: dict[str, Callable[[AnalysisContext], list[Finding]]] = {
    "undefined_reference": detect_undefined_references,
    "dangling_requires": detect_dangling_requires,
    "duplicate_interface": detect_duplicate_interfaces,
    "conflicting_providers": detect_conflicting_providers,
    "orphan_entity": detect_orphan_entities,
    "unconsumed_signal": detect_unconsumed_signals,
}
