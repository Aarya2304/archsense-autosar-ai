"""AUTOSAR extraction validation (M9) — mechanical, no LLM.

Same five-layer philosophy as the M4 validator (D-022):

1. SCHEMA      Pydantic models enforce types/enums/confidence bounds.
2. EVIDENCE    every candidate must carry resolvable evidence; unknown
               evidence IDs reject the candidate (never silently accepted).
3. REFERENCES  both fact endpoints must exist in the candidate entity set
               (the extractor guarantees this by construction; the validator
               enforces it for LLM-supplied candidates later).
4. DOMAIN/RAGE predicate domain/range checked against the AUTOSAR tables.
5. DEDUPE      dedupe_key identity, best confidence wins.

Provenance resolution mirrors M4: the document/version/section/page/chunk
fields come exclusively from the trusted evidence map, never from a
candidate's own claims.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.extraction.autosar.models import (PREDICATE_DOMAIN,
                                               PREDICATE_RANGE, AutosarEntity,
                                               AutosarEntityType,
                                               AutosarFact, AutosarPredicate)
from backend.extraction.models import EvidenceRef, Source


@dataclass
class AutosarIssue:
    kind: str        # unknown_evidence_id | missing_evidence |
                     # unresolvable_reference | domain_violation |
                     # range_violation | below_min_confidence
    detail: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail}


@dataclass
class AutosarValidationStats:
    seen_entities: int = 0
    seen_facts: int = 0
    rejected_entities: int = 0
    rejected_facts: int = 0
    duplicate_facts: int = 0
    unknown_evidence_ids: int = 0

    def to_dict(self) -> dict:
        return {
            "seen_entities": self.seen_entities,
            "seen_facts": self.seen_facts,
            "rejected_entities": self.rejected_entities,
            "rejected_facts": self.rejected_facts,
            "duplicate_facts": self.duplicate_facts,
            "unknown_evidence_ids": self.unknown_evidence_ids,
        }


@dataclass
class ValidatedAutosarExtraction:
    entities: list[AutosarEntity] = field(default_factory=list)
    facts: list[AutosarFact] = field(default_factory=list)
    issues: list[AutosarIssue] = field(default_factory=list)
    stats: AutosarValidationStats = field(
        default_factory=AutosarValidationStats)

    @property
    def ok(self) -> bool:
        return (self.stats.rejected_entities == 0
                and self.stats.rejected_facts == 0)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "entities": [e.model_dump(mode="json") for e in self.entities],
            "facts": [f.model_dump(mode="json") for f in self.facts],
            "issues": [i.to_dict() for i in self.issues],
            "stats": self.stats.to_dict(),
        }


def _resolve(ev: EvidenceRef, emap: dict[str, Source],
             issues: list[AutosarIssue], what: str) -> Source | None:
    if ev.source is not None:
        return ev.source
    eid = ev.evidence_id.strip()
    if not eid:
        issues.append(AutosarIssue(
            "missing_evidence", f"{what} carries no evidence reference"))
        return None
    src = emap.get(eid)
    if src is None:
        issues.append(AutosarIssue(
            "unknown_evidence_id",
            f"{what} references {eid} which is not in the evidence map"))
        return None
    return src


def validate_autosar_extraction(
        candidates: Sequence[AutosarEntity | AutosarFact],
        emap: dict[str, Source],
        min_confidence: float = 0.0) -> ValidatedAutosarExtraction:
    """Validate AUTOSAR extraction candidates against the evidence map."""
    out = ValidatedAutosarExtraction()
    entities: dict[str, AutosarEntity] = {}
    raw_facts: list[AutosarFact] = []

    for cand in candidates:
        if isinstance(cand, AutosarEntity):
            out.stats.seen_entities += 1
            src = _resolve(cand.evidence, emap, out.issues,
                           f"entity {cand.entity_type.value}:{cand.name}")
            if src is None:
                if cand.evidence.evidence_id:
                    out.stats.unknown_evidence_ids += 1
                out.stats.rejected_entities += 1
                continue
            if cand.confidence < min_confidence:
                out.stats.rejected_entities += 1
                out.issues.append(AutosarIssue(
                    "below_min_confidence",
                    f"entity {cand.key} confidence {cand.confidence} < "
                    f"{min_confidence}"))
                continue
            prev = entities.get(cand.key)
            if prev is None or cand.confidence > prev.confidence:
                entities[cand.key] = cand
        else:
            out.stats.seen_facts += 1
            raw_facts.append(cand)

    known = set(entities)

    fact_by_key: dict[str, AutosarFact] = {}
    for f in raw_facts:
        src = _resolve(f.evidence, emap, out.issues,
                       f"fact {f.dedupe_key or (f.subject, f.predicate.value, f.object)}")
        if src is None:
            if f.evidence.evidence_id:
                out.stats.unknown_evidence_ids += 1
            out.stats.rejected_facts += 1
            continue
        if f.confidence < min_confidence:
            out.stats.rejected_facts += 1
            out.issues.append(AutosarIssue(
                "below_min_confidence",
                f"fact {f.dedupe_key} confidence {f.confidence} < "
                f"{min_confidence}"))
            continue

        stype = _type_of(f.subject)
        otype = _type_of(f.object)
        dom = PREDICATE_DOMAIN[f.predicate]
        rng = PREDICATE_RANGE[f.predicate]
        if stype is not None and stype not in dom:
            out.stats.rejected_facts += 1
            out.issues.append(AutosarIssue(
                "domain_violation",
                f"{f.predicate.value} subject {f.subject} is "
                f"{stype.value}, expected one of "
                f"{[t.value for t in dom]}"))
            continue
        if otype is not None and otype not in rng:
            out.stats.rejected_facts += 1
            out.issues.append(AutosarIssue(
                "range_violation",
                f"{f.predicate.value} object {f.object} is "
                f"{otype.value}, expected one of "
                f"{[t.value for t in rng]}"))
            continue
        if f.subject not in known:
            out.stats.rejected_facts += 1
            out.issues.append(AutosarIssue(
                "unresolvable_reference", f"subject {f.subject} is unknown"))
            continue
        if f.object not in known:
            out.stats.rejected_facts += 1
            out.issues.append(AutosarIssue(
                "unresolvable_reference", f"object {f.object} is unknown"))
            continue

        dk = f"{f.subject}|{f.predicate.value}|{f.object}"
        prev = fact_by_key.get(dk)
        if prev is not None:
            out.stats.duplicate_facts += 1
            if f.confidence > prev.confidence:
                fact_by_key[dk] = f
            continue
        fact_by_key[dk] = f

    out.entities = sorted(entities.values(), key=lambda e: e.key)
    out.facts = sorted(fact_by_key.values(), key=lambda f: f.dedupe_key)
    return out


def _type_of(key: str) -> AutosarEntityType | None:
    parts = key.split(":")
    if len(parts) >= 3 and parts[0] == "autosar":
        try:
            return AutosarEntityType(parts[1])
        except ValueError:
            return None
    return None
