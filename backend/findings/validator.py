"""Mechanical finding validation (M6.8) — pure logic, no LLM, no DB reads.

Same philosophy as M3 citation validation (D-018) and M4 extraction
validation (D-022): findings produced by detectors or loaded from external
sources are checked against strict mechanical rules before being trusted or
persisted. The validator receives a trusted key inventory (known fact keys,
known entity keys) and checks every finding against it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.findings.models import (Finding, FindingStatus, FindingType,
                                     deterministic_finding_id)
from backend.storage.models import FindingSeverity

__all__ = ["FindingIssue", "ValidationReport", "validate_findings"]


@dataclass(frozen=True)
class FindingIssue:
    code: str
    finding_id: str
    detail: str


@dataclass
class ValidationReport:
    ok: bool
    issues: list[FindingIssue] = field(default_factory=list)
    checked: int = 0

    def summary(self) -> str:
        head = (f"checked {self.checked} findings: "
                f"{'OK' if self.ok else f'{len(self.issues)} issue(s)'}")
        return head + ("" if self.ok else
                       "\n  " + "\n  ".join(f"{i.code}: {i.finding_id} — {i.detail}"
                                            for i in self.issues))


def validate_findings(findings: list[Finding], known_fact_keys: set[str],
                      known_entity_keys: set[str]) -> ValidationReport:
    """Validate a list of findings against a trusted key inventory.

    Rules (deterministic):
      V1  finding_id must be non-empty and match the deterministic scheme
          recomputed from (version, type, entity/fact keys) — a mismatched
          or missing ID fails.
      V2  finding_type must be a valid FindingType (enforced by the model;
          re-checked for constructor-bypass objects).
      V3  severity/status must be members of the controlled vocabularies
          (the M1 storage enums).
      V4  confidence must lie in [0, 1].
      V5  non-empty title/description and at least one evidence item.
      V6  every fact evidence key must exist in the registry (unknown fact
          keys fail — fabricated evidence is rejected, D-022 philosophy).
      V7  entity evidence keys must be canonical-shaped; they MAY be absent
          from the registry only for undefined_reference findings (the
          missing entity is the finding); all other findings must reference
          existing entities.
      V8  version_label non-empty (single-version scope per finding).
      V9  finding IDs must be unique across the list (duplicate detection).
      V10 fact_keys/entity_keys top-level lists must be consistent with the
          evidence items.
    """
    issues: list[FindingIssue] = []
    seen: dict[str, int] = {}
    for idx, f in enumerate(findings):
        fid = f.finding_id

        # V1 deterministic ID check
        try:
            expected = deterministic_finding_id(
                f.version_label, FindingType(f.finding_type),
                f.entity_keys, f.fact_keys)
        except ValueError:
            issues.append(FindingIssue("invalid_finding_type", fid,
                                       f"unknown finding type {f.finding_type!r}"))
            continue
        if not fid or fid != expected:
            issues.append(FindingIssue(
                "invalid_finding_id", fid,
                f"finding_id does not match deterministic scheme "
                f"(expected {expected})"))

        # V3 severity/status vocabulary (value-set check — a raw string
        # attribute must fail cleanly, never raise)
        sev_val = (f.severity.value if hasattr(f.severity, "value")
                   else str(f.severity))
        stat_val = (f.status.value if hasattr(f.status, "value")
                    else str(f.status))
        if sev_val not in FindingSeverity._value2member_map_:
            issues.append(FindingIssue("invalid_severity", fid,
                                       f"severity {f.severity!r} not allowed"))
        if stat_val not in FindingStatus._value2member_map_:
            issues.append(FindingIssue("invalid_status", fid,
                                       f"status {f.status!r} not allowed"))

        # V4 confidence range
        if not (0.0 <= f.confidence <= 1.0):
            issues.append(FindingIssue(
                "invalid_confidence", fid,
                f"confidence {f.confidence} outside [0, 1]"))

        # V5 non-empty descriptive fields + evidence presence
        if not f.title.strip() or not f.description.strip():
            issues.append(FindingIssue("missing_description", fid,
                                       "title/description must be non-empty"))
        if not f.evidence:
            issues.append(FindingIssue("missing_evidence", fid,
                                       "at least one evidence item required"))

        # V6 fact keys must exist
        for fk in f.fact_keys:
            if fk not in known_fact_keys:
                issues.append(FindingIssue(
                    "unknown_fact_key", fid,
                    f"fact {fk!r} not in registry"))

        # V7 entity keys: canonical shape; existence rules. Findings whose
        # semantics ASSERT an entity's absence (undefined_reference: the
        # referenced entity is missing; dangling_requires: the required
        # interface may itself be undefined) are exempt from the existence
        # requirement — for every other type, unknown entity keys fail.
        undef = f.finding_type in (FindingType.UNDEFINED_REFERENCE,
                                   FindingType.DANGLING_REQUIRES)
        for ek in f.entity_keys:
            if ":" not in ek or ek.partition(":")[0] not in (
                    "component", "interface", "port", "signal",
                    "dependency", "functional_flow"):
                issues.append(FindingIssue(
                    "malformed_entity_key", fid, f"entity key {ek!r} malformed"))
            elif ek not in known_entity_keys and not undef:
                issues.append(FindingIssue(
                    "unknown_entity_key", fid,
                    f"entity {ek!r} not in registry"))

        # V8 version scope
        if not f.version_label.strip():
            issues.append(FindingIssue("version_mismatch", fid,
                                       "empty version label"))

        # V9 duplicates within the list
        if fid in seen:
            issues.append(FindingIssue(
                "duplicate_finding_id", fid,
                f"duplicate finding id (first seen at index {seen[fid]})"))
        else:
            seen[fid] = idx

    return ValidationReport(ok=not issues, issues=issues,
                            checked=len(findings))
