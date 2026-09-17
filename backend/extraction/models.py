"""Typed extraction schema (M4.2/M4.3) — Pydantic v2 models.

The taxonomy is derived from the actual synthetic corpus (D-021), not
invented: every entity type and predicate below is mechanically derivable
from chunk content that exists in both HLD versions:

Entity types ( EntityType )          Corpus source (verified chunk shapes)
  component      C-01..C-20          3.1 catalogue table rows, 3.2.x headings
  interface      IF-01..IF-25        4.x prose "Name (IF-xx)", signal tables
  port           P-001..P-057        3.2.x port tables "P-xxx | direction ..."
  signal         SG-001..SG-034      4.x signal tables, 5 dictionary table
  dependency     DEP-01..DEP-24      6.1 table rows, 6.2.x detail sections
  functional_flow FL-1..FL-5         7.x headings "(FL-n)"

Predicates ( Predicate )             Corpus source
  provides        component  -> interface        3.2.x "P-x | provides | IF"
  requires        component  -> interface        3.2.x "P-x | requires | IF"
  depends_on      component  -> component        6.1 table "depends_on" rows
  carries         interface  -> signal           4.x/5 tables (Signal|...|
                                                      Interface) + dictionary
  implements      port       -> interface        port table "Port|Direction|
                                                      Interface" rows
  participates_in component  -> functional_flow  7.x "Flow: C-x (Name) -> ..."
"""

from __future__ import annotations

import enum
import re
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ------------------------------------------------------------------ enums ----


class EntityType(str, enum.Enum):
    """Entity types supported by the synthetic corpus (D-021)."""

    COMPONENT = "component"
    INTERFACE = "interface"
    PORT = "port"
    SIGNAL = "signal"
    DEPENDENCY = "dependency"
    FUNCTIONAL_FLOW = "functional_flow"


class Predicate(str, enum.Enum):
    """Relationship predicates supported by the corpus (D-021)."""

    PROVIDES = "provides"              # component -> interface
    REQUIRES = "requires"              # component -> interface
    DEPENDS_ON = "depends_on"          # component -> component
    CARRIES = "carries"                # interface  -> signal
    IMPLEMENTS = "implements"          # port       -> interface
    PARTICIPATES_IN = "participates_in"  # component -> functional_flow


# ------------------------------------------------------------- normalization --

_ID_RE = {
    EntityType.COMPONENT: re.compile(r"^[Cc]-\d{1,3}$"),
    EntityType.INTERFACE: re.compile(r"^[Ii][Ff]-\d{1,3}$"),
    EntityType.PORT: re.compile(r"^[Pp]-\d{1,3}$"),
    EntityType.SIGNAL: re.compile(r"^[Ss][Gg]-\d{1,3}$"),
    EntityType.DEPENDENCY: re.compile(r"^[Dd][Ee][Pp]-\d{1,3}$"),
    EntityType.FUNCTIONAL_FLOW: re.compile(r"^[Ff][Ll]-\d{1,2}$"),
}


def normalize_key(entity_type: EntityType, name: str) -> str:
    """Deterministic normalized identifier key (M4.9).

    Prefixed-ID entities collapse case ("c-02" -> "component:c-02"). For
    name-keyed types the key is the lowercased name ("vehiclemodeif" ->
    "interface:vehiclemodeif"). IDs never keep a name suffix: dedupe keys
    stay stable even when an entity's display name changes between
    revisions.
    """
    t = entity_type.value
    n = name.strip()
    for et, rx in _ID_RE.items():
        if et is entity_type:
            if rx.match(n):
                return f"{t}:{n.upper()}"
            break
    return f"{t}:{n.lower()}"


# ------------------------------------------------------------------ models ----


class Source(BaseModel):
    """Trusted provenance — one immutable snapshot of chunk metadata.

    Instances are always created by the application from ``Chunk``/
    ``RetrievedChunk`` metadata or the deterministic extractor's table
    records; LLM output can reference evidence IDs, never these fields
    directly (D-022).
    """

    model_config = ConfigDict(frozen=True)

    document_name: str
    version: str
    sha256: str = ""
    section_no: str = ""
    section_title: str = ""
    page_start: int
    page_end: int
    chunk_id: str

    @property
    def pages_csv(self) -> str:
        lo, hi = self.page_start, self.page_end
        return str(lo) if lo == hi else f"{lo}-{hi}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_name": self.document_name,
            "version": self.version,
            "sha256": self.sha256,
            "section_no": self.section_no,
            "section_title": self.section_title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "pages_csv": self.pages_csv,
            "chunk_id": self.chunk_id,
        }


class EvidenceRef(BaseModel):
    """An evidence pointer attached to an extraction.

    ``evidence_id`` ("E3") resolves against the extraction context's
    trusted evidence map. Either an evidence_id or a direct Source must be
    present; the validator rejects unknown IDs (D-022).
    """

    model_config = ConfigDict(frozen=True)

    evidence_id: str = ""
    source: Source | None = None

    def resolved(self, emap: dict[str, Source]) -> Source | None:
        if self.source is not None:
            return self.source
        return emap.get(self.evidence_id)


class ExtractedEntity(BaseModel):
    """One candidate entity with provenance and confidence."""

    model_config = ConfigDict(frozen=True)

    entity_type: EntityType
    name: str                                  # original surface form
    normalized_name: str = ""                  # filled post-init
    attributes: dict[str, str] = Field(default_factory=dict)
    evidence: EvidenceRef
    confidence: float
    extractor: str = "deterministic"           # "deterministic" | "llm"
    origin_chunk_id: str = ""                  # trusted, set by the app

    @property
    def key(self) -> str:
        """Canonical registry key.

        ID-bearing entities (attributes["id"] present, e.g. catalogue
        components) key on the pure ID ("component:c-01") so facts and
        entities share one key space; name-only entities key on the
        normalized name.
        """
        eid = self.attributes.get("id", "").strip()
        if eid:
            return f"{self.entity_type.value}:{eid.upper()}"
        return normalize_key(self.entity_type,
                             self.normalized_name or self.name)

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

    def model_post_init(self, __context: Any) -> None:
        if not self.normalized_name:
            object.__setattr__(
                self, "normalized_name",
                normalize_key(self.entity_type, self.name).split(":", 1)[1])


class ExtractedFact(BaseModel):
    """One candidate relationship (subject -> predicate -> object).

    ``subject``/``object`` hold normalized keys; ``object_value`` carries a
    literal when the object is a value (not used by the corpus taxonomy
    yet, kept per the M4 schema requirement). ``dedupe_key`` is the
    deterministic duplicate identity (M4.7).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    PREDICATE_DOMAIN: ClassVar[dict[Predicate, EntityType]]
    PREDICATE_RANGE: ClassVar[dict[Predicate, tuple[EntityType, ...]]]

    subject: str
    predicate: Predicate
    object: str = ""
    object_value: str = ""
    evidence: EvidenceRef
    confidence: float
    extractor: str = "deterministic"
    fact_id: str = ""
    origin_chunk_id: str = ""

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
            f"{self.subject}|{self.predicate.value}|{self.object}"
            f"|{self.object_value}")

    @property
    def key(self) -> str:
        return self.subject

    dedupe_key: str = ""


# Attach the class-level type tables (kept out of the dataclass body for
# readability; validated by tests against the taxonomy table in D-021).
ExtractedFact.PREDICATE_DOMAIN = {
    Predicate.PROVIDES: EntityType.COMPONENT,
    Predicate.REQUIRES: EntityType.COMPONENT,
    Predicate.DEPENDS_ON: EntityType.COMPONENT,
    Predicate.CARRIES: EntityType.INTERFACE,
    Predicate.IMPLEMENTS: EntityType.PORT,
    Predicate.PARTICIPATES_IN: EntityType.COMPONENT,
}
ExtractedFact.PREDICATE_RANGE = {
    Predicate.PROVIDES: (EntityType.INTERFACE,),
    Predicate.REQUIRES: (EntityType.INTERFACE,),
    Predicate.DEPENDS_ON: (EntityType.COMPONENT,),
    Predicate.CARRIES: (EntityType.SIGNAL,),
    Predicate.IMPLEMENTS: (EntityType.INTERFACE,),
    Predicate.PARTICIPATES_IN: (EntityType.FUNCTIONAL_FLOW,),
}
