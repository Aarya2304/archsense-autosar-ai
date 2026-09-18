"""Revision-comparison data model (M7): typed, deterministic changes.

Continues the established trust model (D-018/D-022/D-028): a revision
comparison is *derived* knowledge computed mechanically from two
version-scoped M4 registry snapshots + M5 graphs. The LLM plays no role.

Identity rules (documented in D-037):
- entity identity   = canonical M4 key ("component:C-02")
- relationship identity = the M4 fact TRIPLE (subject, predicate, object,
  object_value) — several fact_keys may spell one triple (parallel chunks
  dedupe into one fact_key per version, but the triple is the stable
  semantic identity across revisions; a fact_key change caused by a
  semantic change therefore manifests as REMOVED old + ADDED new, exactly
  as task rule 6 prefers).
- impact identity   = (source change, impacted entity, depth)

All IDs are content-addressed (sha256) — no random UUIDs anywhere.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "ChangeType", "ImpactCategory", "ProvenanceSnapshot",
    "EntityChange", "RelationshipChange", "ImpactItem", "RevisionFinding",
    "RevisionSummary", "RevisionComparison",
    "deterministic_change_id", "deterministic_impact_id",
    "triple_of", "CHANGE_CODES",
]


class ChangeType(str, Enum):
    """The change vocabulary (task rules 4-6, 9; semantics in D-037).

    ENTITY_CHANGED exists because the M4 model keeps stable canonical keys
    across revisions while display names may change (e.g. a component
    rename) — a genuine attribute-level change with unchanged identity.
    RELATIONSHIP_CHANGED is deliberately NOT emitted: fact_key changes when
    semantics change, so those surface as REMOVED + ADDED pairs.
    """

    ENTITY_ADDED = "entity_added"
    ENTITY_REMOVED = "entity_removed"
    ENTITY_CHANGED = "entity_changed"          # identity stable, name changed
    RELATIONSHIP_ADDED = "relationship_added"
    RELATIONSHIP_REMOVED = "relationship_removed"


class ImpactCategory(str, Enum):
    """Controlled impact categories (task rule 13; mapping in D-038).

    The category of an impact item is the class of the FIRST graph edge on
    the traversal path from the change anchor; deeper hops are TRANSITIVE.
    """

    DIRECT = "direct"                          # the changed entity/endpoint itself
    DEPENDENCY = "dependency"                  # reached via depends_on
    INTERFACE_CONSUMER = "interface_consumer"  # reached via requires
    INTERFACE_PROVIDER = "interface_provider"  # reached via provides
    SIGNAL = "signal"                          # reached via carries
    FUNCTIONAL_FLOW = "functional_flow"        # reached via participates_in
    TRANSITIVE = "transitive"                  # deeper hops (depth >= 2)


_PREDICATE_CATEGORY = {
    "depends_on": ImpactCategory.DEPENDENCY,
    "requires": ImpactCategory.INTERFACE_CONSUMER,
    "provides": ImpactCategory.INTERFACE_PROVIDER,
    "carries": ImpactCategory.SIGNAL,
    "participates_in": ImpactCategory.FUNCTIONAL_FLOW,
    "implements": ImpactCategory.TRANSITIVE,   # port plumbing, not arch-level
}


def category_for_predicate(predicate: str) -> ImpactCategory:
    """Deterministic predicate -> impact-category mapping (D-038)."""
    return _PREDICATE_CATEGORY.get(predicate, ImpactCategory.TRANSITIVE)


# ------------------------------------------------------------------ IDs ----

CHANGE_CODES: dict[ChangeType, str] = {
    ChangeType.ENTITY_ADDED: "ENT-ADD",
    ChangeType.ENTITY_REMOVED: "ENT-REM",
    ChangeType.ENTITY_CHANGED: "ENT-CHG",
    ChangeType.RELATIONSHIP_ADDED: "REL-ADD",
    ChangeType.RELATIONSHIP_REMOVED: "REL-REM",
}


def _h10(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


def deterministic_change_id(base_version: str, target_version: str,
                            change_type: ChangeType | str,
                            identity: str) -> str:
    """Content-addressed change ID (reproducible; no random UUIDs)."""
    ct = ChangeType(change_type)
    payload = "|".join([base_version, target_version, ct.value, identity])
    return f"M7-{CHANGE_CODES[ct]}-{_h10(payload)}"


def deterministic_impact_id(source_change_id: str, impacted_key: str,
                            depth: int) -> str:
    payload = "|".join([source_change_id, impacted_key, str(depth)])
    return f"M7-IMP-{_h10(payload)}"


def triple_of(fact_key: str) -> tuple[str, str, str, str]:
    """The semantic triple behind an M4 fact_key.

    fact_key layout is ``subject|predicate|object|object_value``; the
    triple identity used for cross-version comparison is the full 4-tuple
    (object_value participates in identity for value-bearing facts).
    """
    parts = fact_key.split("|")
    while len(parts) < 4:
        parts.append("")
    return (parts[0], parts[1], parts[2], parts[3])


# ----------------------------------------------------------- provenance ----


class ProvenanceSnapshot(BaseModel):
    """Trusted provenance copied verbatim from M4 registry columns (D-028).

    Built only by the diff context from ``ExtractionFact`` / typed rows —
    never from LLM output. For REMOVED items this is base-version
    provenance, for ADDED items target-version provenance, for CHANGED
    items both are preserved on the change.
    """

    model_config = {"frozen": True}

    document_name: str = ""
    version_label: str = ""
    section_no: str = ""
    section_title: str = ""
    page_start: int = 0
    page_end: int = 0
    source_chunk_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        pages = (str(self.page_start) if self.page_start == self.page_end
                 else f"{self.page_start}-{self.page_end}")
        return {"document_name": self.document_name,
                "version_label": self.version_label,
                "section_no": self.section_no,
                "section_title": self.section_title,
                "page_start": self.page_start, "page_end": self.page_end,
                "pages_csv": pages, "source_chunk_id": self.source_chunk_id}

    @classmethod
    def from_fact(cls, row) -> "ProvenanceSnapshot":
        return cls(document_name=row.document_name,
                   version_label=row.version_label,
                   section_no=row.section_no,
                   section_title=row.section_title,
                   page_start=row.page_start, page_end=row.page_end,
                   source_chunk_id=row.source_chunk_id)


# --------------------------------------------------------------- changes ----


class EntityChange(BaseModel):
    """One added/removed/name-changed entity (identity = canonical key)."""

    change_id: str = ""
    change_type: ChangeType
    entity_key: str                     # canonical M4 key
    entity_type: str
    display_name: str                   # target name for ADD/CHG, base for REM
    base_version: str
    target_version: str
    base_name: str = ""                 # CHG only
    target_name: str = ""               # CHG only
    confidence: float = 0.0
    provenance: list[ProvenanceSnapshot] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def _conf(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return round(v, 4)

    def sort_key(self) -> tuple:
        return (self.change_type.value, self.entity_type, self.entity_key,
                self.change_id)


class RelationshipChange(BaseModel):
    """One added/removed relationship (identity = the fact triple)."""

    change_id: str = ""
    change_type: ChangeType
    fact_key: str                       # representative M4 fact_key
    subject: str
    predicate: str
    object: str
    object_value: str = ""
    base_version: str
    target_version: str
    confidence: float = 0.0
    extractor: str = "deterministic"
    provenance: list[ProvenanceSnapshot] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def _conf(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return round(v, 4)

    @property
    def triple(self) -> tuple[str, str, str, str]:
        return (self.subject, self.predicate, self.object, self.object_value)

    def sort_key(self) -> tuple:
        return (self.change_type.value, self.triple, self.change_id)


# ---------------------------------------------------------------- impact ----


class PathStep(BaseModel):
    """One REAL graph edge on an impact path, recorded in walk order.

    ``direction`` distinguishes traversal along the edge's direction
    ("forward": edge is frm->to) from traversal against it ("reverse":
    the stored edge is to->frm; we walked frm by following that edge
    backwards). Both are existing edges only - no invented hops.
    """

    model_config = {"frozen": True}

    frm: str
    predicate: str
    to: str
    fact_key: str
    direction: str = "forward"   # "forward" | "reverse"

    def render(self) -> str:
        if self.direction == "reverse":
            return f"{self.frm} <--{self.predicate}-- {self.to}"
        return f"{self.frm} --{self.predicate}--> {self.to}"


class ImpactItem(BaseModel):
    """One potentially-impacted entity reached by deterministic traversal.

    ``reason`` uses the task's 'potentially impacted' phrasing — impact is
    a graph-neighborhood statement, never a claim of functional breakage.
    """

    impact_id: str = ""
    source_change_id: str
    impacted_entity_key: str
    entity_type: str
    category: ImpactCategory
    reason: str
    path: list[PathStep] = Field(default_factory=list)
    depth: int = Field(ge=0)
    version_scope: str                 # graph the path lives in

    def sort_key(self) -> tuple:
        return (self.source_change_id, self.impacted_entity_key, self.depth,
                self.category.value)


# ------------------------------------------------------- revision finding ----


class RevisionFinding(BaseModel):
    """A revision-level architectural issue (NOT every change is one).

    Produced only where an explicit deterministic rule establishes a
    problem (task rule 32) — e.g. a target fact referencing an entity that
    existed in base and was removed in target (STALE_REFERENCE). Changes
    themselves are reported as changes, never auto-elevated to errors.
    """

    finding_id: str = ""
    finding_type: str                   # e.g. "stale_reference"
    base_version: str
    target_version: str
    severity: str                       # high | medium | low | info
    title: str
    description: str
    entity_keys: list[str] = Field(default_factory=list)
    fact_keys: list[str] = Field(default_factory=list)
    change_ids: list[str] = Field(default_factory=list)
    provenance: list[ProvenanceSnapshot] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def sort_key(self) -> tuple:
        return (self.finding_type, tuple(sorted(self.entity_keys)),
                self.finding_id)


# --------------------------------------------------------------- summary ----


class RevisionSummary(BaseModel):
    entity_added: int = 0
    entity_removed: int = 0
    entity_changed: int = 0
    relationship_added: int = 0
    relationship_removed: int = 0
    impact_count: int = 0
    impacts_by_category: dict[str, int] = Field(default_factory=dict)
    relationship_changes_by_predicate: dict[str, int] = Field(
        default_factory=dict)
    entity_changes_by_type: dict[str, int] = Field(default_factory=dict)
    revision_finding_count: int = 0

    def to_dict(self) -> dict:
        return self.model_dump()


# ----------------------------------------------------------- comparison ----


class RevisionComparison(BaseModel):
    """The complete deterministic comparison of exactly two versions."""

    base_version: str
    target_version: str
    document_name: str = ""
    entity_changes: list[EntityChange] = Field(default_factory=list)
    relationship_changes: list[RelationshipChange] = Field(
        default_factory=list)
    impacts: list[ImpactItem] = Field(default_factory=list)
    revision_findings: list[RevisionFinding] = Field(default_factory=list)
    summary: RevisionSummary = Field(default_factory=RevisionSummary)
    validation: dict[str, Any] = Field(default_factory=dict)
    timings_ms: dict[str, float] = Field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")

    def to_json(self, indent: int = 2) -> str:
        import json
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)
