"""Finding data model (M6): typed, deterministic, provenance-bearing.

Philosophy (continues D-018/D-022/D-028): findings are *derived* knowledge
computed mechanically from the trusted M4 registry / M5 graph — the LLM is
never the source of truth for whether a finding exists. Every finding
carries:

- a deterministic content-addressed ``finding_id`` (same version + type +
  entity/fact keys  =>  same ID, always; no random UUIDs),
- trusted provenance copied verbatim from M4/M5 registry metadata,
- rule-based confidence inherited from the underlying facts/entities
  (documented aggregation, NOT a calibrated probability),
- a controlled severity and status vocabulary reusing the existing M1
  storage enums (``FindingSeverity``: high/medium/low/info — the prompt's
  "error/warning/info" sketch maps onto high/medium/info; ``FindingStatus``:
  open/accepted/rejected/needs_discussion) so the M1 schema stays canonical.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.storage.models import FindingSeverity, FindingStatus

__all__ = [
    "FindingType",
    "Finding",
    "EvidenceItem",
    "TYPE_CODES",
    "deterministic_finding_id",
    "CANONICAL_KEY_RE",
]


class FindingType(str, Enum):
    """The six M6 detector categories (task taxonomy; no invented types)."""

    UNDEFINED_REFERENCE = "undefined_reference"
    DANGLING_REQUIRES = "dangling_requires"
    DUPLICATE_INTERFACE = "duplicate_interface"
    CONFLICTING_PROVIDERS = "conflicting_providers"
    ORPHAN_ENTITY = "orphan_entity"
    UNCONSUMED_SIGNAL = "unconsumed_signal"


# short deterministic type codes used inside finding IDs (<= 9 chars each)
TYPE_CODES: dict[FindingType, str] = {
    FindingType.UNDEFINED_REFERENCE: "UNDEFREF",
    FindingType.DANGLING_REQUIRES: "DANGREQ",
    FindingType.DUPLICATE_INTERFACE: "DUPINTF",
    FindingType.CONFLICTING_PROVIDERS: "CONFLPROV",
    FindingType.ORPHAN_ENTITY: "ORPHAN",
    FindingType.UNCONSUMED_SIGNAL: "UNCSIG",
}

# canonical entity/fact key shape: "<entity_type>:<id>" (lowercase type)
CANONICAL_KEY_RE = __import__("re").compile(
    r"^(component|interface|port|signal|dependency|functional_flow):[A-Za-z0-9_.\-]+$")


def deterministic_finding_id(version_label: str,
                             finding_type: FindingType | str,
                             entity_keys: list[str] | tuple[str, ...],
                             fact_keys: list[str] | tuple[str, ...]) -> str:
    """Content-addressed finding ID (fits the M1 String(32) column).

    Same (version, finding type, entity keys, fact keys) always produce the
    same ID -> re-running M6 yields identical IDs, so persistence is
    idempotent and cross-run comparison (M7) is a set operation.
    """
    ft = FindingType(finding_type)
    payload = "|".join([
        str(version_label),
        ft.value,
        ",".join(sorted(set(entity_keys))),
        ",".join(sorted(set(fact_keys))),
    ])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]
    return f"M6-{TYPE_CODES[ft]}-{digest}"


class EvidenceItem(BaseModel):
    """One traceable evidence pointer behind a finding.

    ``kind="fact"`` items reference an ``extraction_facts.fact_key`` (must
    exist in the registry — validated mechanically). ``kind="entity"`` items
    reference a canonical entity key which may deliberately NOT exist in the
    registry (e.g. the missing entity of an UNDEFINED_REFERENCE finding).
    """

    kind: str  # "fact" | "entity"
    key: str
    note: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("fact", "entity"):
            raise ValueError(f"evidence kind must be 'fact' or 'entity', got {v!r}")
        return v


class Finding(BaseModel):
    """A single deterministic architecture finding (M6).

    ``finding_id`` may be omitted; it is then computed deterministically
    from (version, type, entity/fact keys). Supplying a non-deterministic ID
    is not an error at construction (external sources may carry their own
    IDs) but fails mechanical validation (V1).
    """

    finding_id: str | None = None
    version_label: str
    finding_type: FindingType
    severity: FindingSeverity
    title: str
    description: str
    status: FindingStatus = FindingStatus.OPEN
    confidence: float = Field(ge=0.0, le=1.0)
    entity_keys: list[str] = Field(default_factory=list)
    fact_keys: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    # trusted provenance snapshots (M4/M5 registry metadata; D-028) —
    # one dict per evidence source: document/version/section/pages/chunk
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    detector: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": False}

    @model_validator(mode="after")
    def _compute_id(self) -> "Finding":
        if not self.finding_id:
            object.__setattr__(  # plain assignment on BaseModel is fine too
                self, "finding_id", deterministic_finding_id(
                    self.version_label, self.finding_type,
                    self.entity_keys, self.fact_keys))
        return self

    def sort_key(self) -> tuple:
        """Deterministic ordering: type, then entity keys, then fact keys."""
        return (self.finding_type.value, tuple(sorted(self.entity_keys)),
                tuple(sorted(self.fact_keys)), self.finding_id)

    def to_dict(self) -> dict:
        d = self.model_dump(mode="json")
        return d
