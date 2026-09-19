"""AUTOSAR Adaptive Platform structured schema (M9).

Every entity type and predicate below is derived from VERIFIED content of
the real corpus document (AUTOSAR_EXP_PlatformDesign.pdf, R20-11, Document
ID 706) — the same evidence-first rule as the M4 synthetic taxonomy (D-021).
Verified prose shapes that motivate each type/predicate:

Entity types (EntityType)        corpus source (verified prose)
  adaptive_application   "Adaptive Applications (AA) run on top of ARA" (p15)
  functional_cluster     "ARA consists of application interfaces provided
                          by Functional Clusters" (p15); sections 4-12 are
                          per-FC chapters (OS, EM, CM, DM, ...).
  ara                    "AUTOSAR Runtime for Adaptive applications" (p15)
  platform_foundation    "Adaptive Platform Foundation provides fundamental
                          functionalities of AP" (p15)
  platform_service       "Adaptive Platform Services provide platform
                          standard services" (p15)
  machine                "The AP regards hardware it runs on as a Machine"
                          (p19)
  process                "Each AA is implemented as an independent process"
                          (p17)
  manifest               "Machine Manifest ... Execution manifest ...
                          Service Instance Manifest" (pp21-23)
  service                "The notion of a service means functionality
                          provided to applications" (p33)
  interface              "they just provide specified C++ interface" (p15);
                          "ara::com service interface" (p36)
  software_package       "Software Package" UCM updates (p50, section 13.3)

Predicates (Predicate)           verified prose source
  provides_interface     functional_cluster -> interface
                         "application interfaces provided by Functional
                         Clusters" (p15)
  interacts_with         FC/AA -> FC/AA
                         "The SM also interact with other FCs" (p16);
                         "Functional Clusters may interact with each other"
                         (p18); "SM ... interact with Adaptive Applications"
                         (p36)
  commands               SM -> EM  "State Management (SM), is the controller,
                         commanding EM" (p16)
  runs_on                entity -> ara / machine
                         "Adaptive Applications (AA) run on top of ARA" (p15)
  belongs_to             functional_cluster -> foundation/service
                         "which belong to either Adaptive Platform Foundation
                         or Adaptive Platform Services" (p15)
  provides_service       entity -> service
                         "Any AA can also provide Services to other AA" (p15)
  implemented_as         AA/FC -> process
                         "Each AA is implemented as an independent process"
                         (p17); "Functional Clusters are also typically
                         implemented as processes" (p17)
  configured_by          entity -> manifest
                         "based on Machine Manifest and Execution manifest
                         information" (p26); "Recovery Actions ... configured
                         in the Execution Manifest" (p29)
  uses_interface         entity -> interface
                         "The SM should use only the standard ARA interface"
                         (p16); "shall use PSE51 as OS interface" (p24)
  updates                UCM -> software_package (section 13.3 "Software
                         package" processing)

Everything here mirrors backend.extraction.models structurally (Pydantic,
frozen, confidence-validated) but is deliberately SEPARATE: mixing the two
vocabularies in one enum would silently loosen the ABC-HLD domain/range
model that M4-M7 validate against.
"""

from __future__ import annotations

import enum
import re
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.extraction.models import EvidenceRef


class AutosarEntityType(str, enum.Enum):
    """Entity vocabulary derived from the real R20-11 document."""

    ADAPTIVE_APPLICATION = "adaptive_application"
    FUNCTIONAL_CLUSTER = "functional_cluster"
    ARA = "ara"
    PLATFORM_FOUNDATION = "platform_foundation"
    PLATFORM_SERVICE = "platform_service"
    MACHINE = "machine"
    PROCESS = "process"
    MANIFEST = "manifest"
    SERVICE = "service"
    INTERFACE = "interface"
    SOFTWARE_PACKAGE = "software_package"


class AutosarPredicate(str, enum.Enum):
    """Relationship vocabulary derived from the real R20-11 document."""

    PROVIDES_INTERFACE = "provides_interface"   # fc -> interface
    INTERACTS_WITH = "interacts_with"           # fc/aa -> fc/aa
    COMMANDS = "commands"                       # sm -> em
    RUNS_ON = "runs_on"                         # * -> ara | machine
    BELONGS_TO = "belongs_to"                   # fc -> foundation|service
    PROVIDES_SERVICE = "provides_service"       # aa/fc -> service
    IMPLEMENTED_AS = "implemented_as"           # aa/fc -> process
    CONFIGURED_BY = "configured_by"             # * -> manifest
    USES_INTERFACE = "uses_interface"           # * -> interface
    UPDATES = "updates"                         # ucm -> software_package


# domain/range model — mechanically validated (same rule shape as M4.7)
PREDICATE_DOMAIN: dict[AutosarPredicate, tuple[AutosarEntityType, ...]] = {
    AutosarPredicate.PROVIDES_INTERFACE: (
        AutosarEntityType.FUNCTIONAL_CLUSTER,),
    AutosarPredicate.INTERACTS_WITH: (
        AutosarEntityType.FUNCTIONAL_CLUSTER,
        AutosarEntityType.ADAPTIVE_APPLICATION),
    AutosarPredicate.COMMANDS: (AutosarEntityType.FUNCTIONAL_CLUSTER,),
    AutosarPredicate.RUNS_ON: (
        AutosarEntityType.ADAPTIVE_APPLICATION,
        AutosarEntityType.FUNCTIONAL_CLUSTER),
    AutosarPredicate.BELONGS_TO: (
        AutosarEntityType.FUNCTIONAL_CLUSTER,),
    AutosarPredicate.PROVIDES_SERVICE: (
        AutosarEntityType.ADAPTIVE_APPLICATION,
        AutosarEntityType.FUNCTIONAL_CLUSTER),
    AutosarPredicate.IMPLEMENTED_AS: (
        AutosarEntityType.ADAPTIVE_APPLICATION,
        AutosarEntityType.FUNCTIONAL_CLUSTER),
    AutosarPredicate.CONFIGURED_BY: (
        AutosarEntityType.ADAPTIVE_APPLICATION,
        AutosarEntityType.FUNCTIONAL_CLUSTER,
        AutosarEntityType.MACHINE),
    AutosarPredicate.USES_INTERFACE: (
        AutosarEntityType.ADAPTIVE_APPLICATION,
        AutosarEntityType.FUNCTIONAL_CLUSTER),
    AutosarPredicate.UPDATES: (AutosarEntityType.FUNCTIONAL_CLUSTER,),
}

PREDICATE_RANGE: dict[AutosarPredicate, tuple[AutosarEntityType, ...]] = {
    AutosarPredicate.PROVIDES_INTERFACE: (AutosarEntityType.INTERFACE,),
    AutosarPredicate.INTERACTS_WITH: (
        AutosarEntityType.FUNCTIONAL_CLUSTER,
        AutosarEntityType.ADAPTIVE_APPLICATION),
    AutosarPredicate.COMMANDS: (AutosarEntityType.FUNCTIONAL_CLUSTER,),
    AutosarPredicate.RUNS_ON: (
        AutosarEntityType.ARA, AutosarEntityType.MACHINE),
    AutosarPredicate.BELONGS_TO: (
        AutosarEntityType.PLATFORM_FOUNDATION,
        AutosarEntityType.PLATFORM_SERVICE),
    AutosarPredicate.PROVIDES_SERVICE: (AutosarEntityType.SERVICE,),
    AutosarPredicate.IMPLEMENTED_AS: (AutosarEntityType.PROCESS,),
    AutosarPredicate.CONFIGURED_BY: (AutosarEntityType.MANIFEST,),
    AutosarPredicate.USES_INTERFACE: (AutosarEntityType.INTERFACE,),
    AutosarPredicate.UPDATES: (AutosarEntityType.SOFTWARE_PACKAGE,),
}


def normalize_key(entity_type: AutosarEntityType, name: str) -> str:
    """Deterministic canonical key: ``autosar:<type>:<normalized name>``.

    The ``autosar:`` namespace prefix keeps uploaded-AUTOSAR keys disjoint
    from the synthetic ABC keys in any shared tooling; names normalize to
    lower snake case (deterministic, no ID-bearing entities exist in this
    profile — the vocabulary is name-addressed prose architecture).
    """
    n = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return f"autosar:{entity_type.value}:{n}"


# ------------------------------------------------------------------ models ----


class AutosarSource(BaseModel):
    """Trusted provenance snapshot (same fields as M4 ``Source``)."""

    model_config = ConfigDict(frozen=True)

    document_name: str
    version: str
    sha256: str = ""
    section_no: str = ""
    section_title: str = ""
    page_start: int
    page_end: int
    chunk_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_name": self.document_name,
            "version": self.version,
            "sha256": self.sha256,
            "section_no": self.section_no,
            "section_title": self.section_title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "chunk_id": self.chunk_id,
        }


class AutosarEntity(BaseModel):
    """One candidate AUTOSAR entity with provenance and confidence."""

    model_config = ConfigDict(frozen=True)

    entity_type: AutosarEntityType
    name: str
    normalized_name: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    evidence: EvidenceRef
    confidence: float
    extractor: str = "deterministic"
    origin_chunk_id: str = ""

    @field_validator("confidence")
    @classmethod
    def _conf_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return round(v, 4)

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("entity name must be non-empty")
        return v

    @property
    def key(self) -> str:
        return normalize_key(self.entity_type,
                             self.normalized_name or self.name)

    def model_post_init(self, __context: Any) -> None:
        if not self.normalized_name:
            object.__setattr__(
                self, "normalized_name",
                normalize_key(self.entity_type, self.name).split(":", 2)[2])


class AutosarFact(BaseModel):
    """One candidate relationship with domain/range-validated endpoints."""

    model_config = ConfigDict(frozen=True)

    subject: str                 # canonical autosar:* key
    predicate: AutosarPredicate
    object: str                  # canonical autosar:* key
    evidence: EvidenceRef
    confidence: float
    extractor: str = "deterministic"
    origin_chunk_id: str = ""
    dedupe_key: str = ""

    @field_validator("confidence")
    @classmethod
    def _conf_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return round(v, 4)

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "subject", self.subject.strip())
        object.__setattr__(self, "object", self.object.strip())
        object.__setattr__(
            self, "dedupe_key",
            f"{self.subject}|{self.predicate.value}|{self.object}")


# confidence tiers (same rule-based philosophy as M4 D-024; NOT probabilities)
CONF_STRUCTURAL_SENTENCE = 0.90   # explicit definitional sentence
CONF_RELATION_VERB = 0.85         # explicit relationship verb between two
                                  # named entities in one sentence
CONF_NAME_MENTION = 0.75          # named entity introduced in prose
CONF_SECTION_TITLE = 0.80         # entity defined by a section heading
