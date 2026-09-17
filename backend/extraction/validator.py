"""Mechanical extraction validator (M4.7) — pure logic, never calls the LLM.

Validates candidate entities/facts in five layers, mirroring the M3 citation
validator philosophy (D-022):

1. SCHEMA      — Pydantic models already enforce types/enums/ranges; fields
                 like empty names or out-of-range confidence cannot even
                 construct a model (validation errors are collected here).
2. REFERENCES  — every fact's subject/object must resolve to a known entity
                 key (the candidate set plus alias map), or the fact is
                 rejected with a recorded issue.
3. EVIDENCE    — every entity/fact must carry resolvable evidence; unknown
                 evidence IDs fail hard (never silently accepted, D-022).
4. PROVENANCE  — document/version/section/page/chunk come exclusively from
                 the trusted evidence map; LLM output can never supply them.
5. CONFIDENCE  — bounds enforced by the schema; the min-confidence floor is
                 applied here as a documented rejection threshold.

Duplicate facts are deduplicated deterministically afterwards (dedupe_key,
best confidence wins; duplicates counted in stats).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from backend.extraction.models import (EntityType, EvidenceRef, ExtractedEntity,
                                       ExtractedFact, Predicate, Source,
                                       normalize_key)

_ID_LIKE_RE = re.compile(r"^[A-Za-z]{1,4}-\d{1,3}$")

# Entity types that are registry-first (ID-keyed); facts about them may use
# name keys which the service canonicalizes via aliases before validation.
_CANONICALIZABLE = {EntityType.COMPONENT, EntityType.INTERFACE,
                    EntityType.SIGNAL}


# ------------------------------------------------------------------ issues ----

@dataclass
class ExtractionIssue:
    """One validation problem (surfaced to caller / audit; never hidden)."""

    kind: str          # unknown_evidence_id | unresolvable_reference |
                       # invalid_entity_type | invalid_predicate |
                       # invalid_confidence | missing_evidence |
                       # below_min_confidence | domain_violation |
                       # range_violation | construction_error
    detail: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail}


@dataclass
class ValidationStats:
    seen_entities: int = 0
    seen_facts: int = 0
    rejected_entities: int = 0
    rejected_facts: int = 0
    duplicate_facts: int = 0
    unknown_evidence_ids: int = 0
    below_min_confidence: int = 0

    def to_dict(self) -> dict:
        return {
            "seen_entities": self.seen_entities,
            "seen_facts": self.seen_facts,
            "rejected_entities": self.rejected_entities,
            "rejected_facts": self.rejected_facts,
            "duplicate_facts": self.duplicate_facts,
            "unknown_evidence_ids": self.unknown_evidence_ids,
            "below_min_confidence": self.below_min_confidence,
        }


@dataclass
class ValidatedExtraction:
    """Validator output: accepted, provenance-resolved, deduped extractions."""

    entities: list[ExtractedEntity] = field(default_factory=list)
    facts: list[ExtractedFact] = field(default_factory=list)
    issues: list[ExtractionIssue] = field(default_factory=list)
    stats: ValidationStats = field(default_factory=ValidationStats)

    @property
    def ok(self) -> bool:
        """True when nothing was rejected (audit-friendly summary flag)."""
        return (self.stats.rejected_entities == 0
                and self.stats.rejected_facts == 0)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "entities": [e.model_dump() for e in self.entities],
            "facts": [f.model_dump() for f in self.facts],
            "issues": [i.to_dict() for i in self.issues],
            "stats": self.stats.to_dict(),
        }


# ---------------------------------------------------------------- validator ----

def _resolve(ev: EvidenceRef, emap: dict[str, Source],
             issues: list[ExtractionIssue], what: str) -> Source | None:
    """Resolve an evidence reference against the trusted map (M4.6).

    Returns None (and records an issue) when the reference is unknown or
    missing — the candidate is rejected, never silently accepted.
    """
    if ev.source is not None:                 # deterministic path
        return ev.source
    eid = ev.evidence_id.strip()
    if not eid:
        issues.append(ExtractionIssue(
            "missing_evidence", f"{what} carries no evidence reference"))
        return None
    src = emap.get(eid)
    if src is None:
        issues.append(ExtractionIssue(
            "unknown_evidence_id",
            f"{what} references {eid} which is not in the evidence map"))
        return None
    return src


def _canonical_key(et: EntityType, name: str, aliases: dict[str, str]) -> str:
    """Canonical key for a candidate (name -> ID via aliases when known)."""
    key = normalize_key(et, name)
    return aliases.get(key, key)


def _known_key(key: str, known: set[str]) -> bool:
    if key in known:
        return True
    # ID-like surface forms ("c-08" -> "component:c-08" already handled by
    # normalize_key; tolerate raw-ID objects missing the type prefix).
    return key.split(":", 1)[-1].upper() in {k.split(":", 1)[-1].upper()
                                             for k in known}


def validate_extraction(candidates: Sequence[ExtractedEntity | ExtractedFact],
                        emap: dict[str, Source],
                        aliases: dict[str, str] | None = None,
                        known_keys: set[str] | None = None,
                        min_confidence: float = 0.0) -> ValidatedExtraction:
    """Validate candidate extractions (M4.7).

    ``candidates`` may mix entities and facts; they are separated by type.
    ``emap`` is the trusted evidence-ID -> Source map from the extraction
    context. ``aliases`` maps name-keys to canonical ID-keys (from the
    deterministic extractor). ``known_keys`` optionally extends the
    reference-resolving universe (e.g. the registry from a prior run).
    ``min_confidence`` is the documented rejection floor (D-024).
    """
    aliases = aliases or {}
    known = set(known_keys or ())
    out = ValidatedExtraction()
    raw_entities: list[ExtractedEntity] = []
    raw_facts: list[ExtractedFact] = []

    for cand in candidates:
        if isinstance(cand, ExtractedEntity):
            out.stats.seen_entities += 1
            raw_entities.append(cand)
        else:
            out.stats.seen_facts += 1
            raw_facts.append(cand)

    # ---------------- entities: evidence + confidence + canonical keys ----
    ent_keys: set[str] = set()
    # Canonical entity view (ID-keyed when available) + alias merge, so the
    # fact-reference check and the dedupe universe share one key space.
    canonical: dict[str, ExtractedEntity] = {}
    for e in raw_entities:
        src = _resolve(e.evidence, emap, out.issues,
                       f"entity {e.entity_type.value}:{e.name}")
        if src is None:
            if e.evidence.evidence_id:
                out.stats.unknown_evidence_ids += 1
            out.stats.rejected_entities += 1
            continue
        if e.confidence < min_confidence:
            out.stats.below_min_confidence += 1
            out.stats.rejected_entities += 1
            out.issues.append(ExtractionIssue(
                "below_min_confidence",
                f"entity {e.key} confidence {e.confidence} < {min_confidence}"))
            continue
        key = _canonical_key(e.entity_type,
                             e.attributes.get("id") or e.name, aliases)
        canonical.setdefault(key, e)
        if e.confidence > canonical[key].confidence:
            canonical[key] = e
        ent_keys.add(key)

    known_entity_keys = ent_keys | known

    # ---------------- facts: references + evidence + confidence -----------
    fact_by_key: dict[str, ExtractedFact] = {}
    for f in raw_facts:
        src = _resolve(f.evidence, emap, out.issues, f"fact {f.dedupe_key}")
        if src is None:
            if f.evidence.evidence_id:
                out.stats.unknown_evidence_ids += 1
            out.stats.rejected_facts += 1
            continue
        if f.confidence < min_confidence:
            out.stats.below_min_confidence += 1
            out.stats.rejected_facts += 1
            out.issues.append(ExtractionIssue(
                "below_min_confidence",
                f"fact {f.dedupe_key} confidence {f.confidence} < "
                f"{min_confidence}"))
            continue

        subj_key = _canonical_key(*_split(f.subject), aliases) \
            if ":" in f.subject else f.subject
        obj_key = _canonical_key(*_split(f.object), aliases) \
            if ":" in f.object else f.object

        dom = ExtractedFact.PREDICATE_DOMAIN[f.predicate]
        rng = ExtractedFact.PREDICATE_RANGE[f.predicate]
        subj_type = _type_of(subj_key)
        obj_type = _type_of(obj_key)
        if subj_type is not None and subj_type is not dom:
            out.stats.rejected_facts += 1
            out.issues.append(ExtractionIssue(
                "domain_violation",
                f"{f.predicate.value} subject {f.subject} is "
                f"{subj_type.value}, expected {dom.value}"))
            continue
        if obj_type is not None and obj_type not in rng:
            out.stats.rejected_facts += 1
            out.issues.append(ExtractionIssue(
                "range_violation",
                f"{f.predicate.value} object {f.object} is "
                f"{obj_type.value}, expected one of "
                f"{[t.value for t in rng]}"))
            continue
        if subj_key not in known_entity_keys and not _loose_known(subj_key, known_entity_keys):
            out.stats.rejected_facts += 1
            out.issues.append(ExtractionIssue(
                "unresolvable_reference", f"subject {f.subject} is unknown"))
            continue
        if obj_key not in known_entity_keys and not _loose_known(obj_key, known_entity_keys):
            out.stats.rejected_facts += 1
            out.issues.append(ExtractionIssue(
                "unresolvable_reference", f"object {f.object} is unknown"))
            continue

        # Reconstruct (not model_copy) so dedupe_key is recomputed from
        # the canonical keys — model_copy would keep the pre-canonical
        # surface-form key and break the registry's dedupe identity.
        kept = ExtractedFact(
            subject=subj_key, predicate=f.predicate, object=obj_key,
            object_value=f.object_value, evidence=f.evidence,
            confidence=f.confidence, extractor=f.extractor,
            fact_id=f.fact_id, origin_chunk_id=f.origin_chunk_id)
        if kept.dedupe_key in fact_by_key:
            out.stats.duplicate_facts += 1
            prev = fact_by_key[kept.dedupe_key]
            if kept.confidence > prev.confidence:
                fact_by_key[kept.dedupe_key] = kept
            continue
        fact_by_key[kept.dedupe_key] = kept

    out.facts = sorted(fact_by_key.values(),
                       key=lambda f: (f.dedupe_key,))
    out.entities = sorted(canonical.values(), key=lambda e: e.key)
    return out


def _split(key: str) -> tuple[EntityType, str]:
    etype, _, name = key.partition(":")
    return EntityType(etype), name


def _type_of(key: str) -> EntityType | None:
    etype, _, _ = key.partition(":")
    try:
        return EntityType(etype)
    except ValueError:
        return None


def _loose_known(key: str, known: set[str]) -> bool:
    """Fallback matcher for name-form references before alias resolution."""
    tail = key.split(":", 1)[-1].lower()
    return any(k.split(":", 1)[-1].lower() == tail for k in known)
