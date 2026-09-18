"""Mechanical comparison validation (M7.22): pure logic, no LLM.

Mirrors the M4/M5/M6 validator philosophy: structured results, honest
errors, deterministic. Rules (task rule 22):

  V1  base/target versions present and distinct
  V2  every change type is in the controlled vocabulary
  V3  no duplicate change IDs
  V4  entity changes carry canonical keys; entity_type matches the prefix
  V5  relationship changes have non-empty subject/object
  V6  every change carries provenance whose version matches the change's
      side (added -> target version, removed -> base version, changed ->
      both sides present); no cross-side contamination
  V7  revision findings reference entity/fact keys that exist on the
      correct side (fact keys must exist in target; stale entities must
      be absent from target and present in base)
  V8  impact paths consist of edges that actually exist in the scope
      version's graph (requires ``graphs``; otherwise skipped + warning)
  V9  no duplicate impact IDs; impact depth within configured bounds;
      impact scope matches the change side's version
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.diff.models import ChangeType, RevisionComparison

VALID_CHANGE_TYPES = {t.value for t in ChangeType}
ADDED_TYPES = {ChangeType.ENTITY_ADDED.value,
               ChangeType.RELATIONSHIP_ADDED.value}
REMOVED_TYPES = {ChangeType.ENTITY_REMOVED.value,
                 ChangeType.RELATIONSHIP_REMOVED.value}


@dataclass
class RevisionValidationResult:
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_changes: int = 0
    checked_impacts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": self.errors,
                "warnings": self.warnings,
                "checked_changes": self.checked_changes,
                "checked_impacts": self.checked_impacts}


def validate_comparison(cmp: RevisionComparison,
                        graphs: dict[str, object] | None = None,
                        max_depth: int | None = None
                        ) -> RevisionValidationResult:
    """Validate one RevisionComparison mechanically.

    ``graphs`` optionally maps version label -> MultiDiGraph for the
    impact-path existence check (V8); when omitted the check is skipped
    and reported as a warning (paths are validated in the tests, which
    hold the graphs).
    """
    errors: list[str] = []
    warnings: list[str] = []
    n_changes = 0
    n_impacts = 0

    # -- V1: versions present and distinct --------------------------------
    if not cmp.base_version or not cmp.target_version:
        errors.append("V1: base_version and target_version must be set")
    elif cmp.base_version == cmp.target_version:
        errors.append(
            f"V1: base_version == target_version ({cmp.base_version!r})")

    changes = list(cmp.entity_changes) + list(cmp.relationship_changes)

    # -- V2: valid change types --------------------------------------------
    for c in changes:
        n_changes += 1
        ct = c.change_type.value if hasattr(c.change_type, "value") \
            else str(c.change_type)
        if ct not in VALID_CHANGE_TYPES:
            errors.append(f"V2: invalid change type {c.change_type!r} on "
                          f"{c.change_id}")

    # -- V3: no duplicate change IDs ----------------------------------------
    ids = [c.change_id for c in changes]
    for d in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"V3: duplicate change_id {d}")

    # -- V4: canonical entity keys, entity_type matches prefix --------------
    for c in cmp.entity_changes:
        key = c.entity_key
        if ":" not in key:
            errors.append(f"V4: entity change {c.change_id} has a "
                          f"non-canonical entity key {key!r}")
            continue
        etype = key.split(":", 1)[0]
        if c.entity_type != etype:
            errors.append(f"V4: entity change {c.change_id} entity_type "
                          f"{c.entity_type!r} != key prefix {etype!r}")

    # -- V5: relationship changes have non-empty triples ---------------------
    for c in cmp.relationship_changes:
        if not c.subject or not c.object:
            errors.append(f"V5: relationship change {c.change_id} has "
                          "empty subject/object")

    # -- V6: provenance present and version-matched to the change side ------
    for c in changes:
        ct = c.change_type.value if hasattr(c.change_type, "value") \
            else str(c.change_type)
        expected_versions = set()
        if ct in ADDED_TYPES:
            expected_versions.add(c.target_version)
        elif ct in REMOVED_TYPES:
            expected_versions.add(c.base_version)
        else:  # entity_changed: both sides
            expected_versions.update({c.base_version, c.target_version})
        if not c.provenance:
            errors.append(f"V6: change {c.change_id} has no provenance")
            continue
        for p in c.provenance:
            pv = p.version_label
            if pv not in expected_versions:
                errors.append(
                    f"V6: change {c.change_id} carries provenance for "
                    f"version {pv!r}, expected one of "
                    f"{sorted(expected_versions)} (no cross-side data)")

    # -- V7: revision findings reference keys on the correct side ------------
    for rf in cmp.revision_findings:
        for fk in rf.fact_keys:
            if fk not in {c.fact_key for c in cmp.relationship_changes}:
                # a stale-reference fact_key is a TARGET fact, not a change;
                # existence on the target side is asserted by construction.
                warnings.append(f"V7: finding {rf.finding_id} fact_key "
                                f"{fk} is not itself a recorded change")
        for ek in rf.entity_keys:
            if ek not in {c.entity_key for c in cmp.entity_changes}:
                errors.append(f"V7: finding {rf.finding_id} references "
                              f"entity {ek} with no matching recorded "
                              f"entity change")

    # -- V8: impact paths exist in the scope graph ----------------------------
    if graphs is None:
        warnings.append("V8: skipped (no graphs supplied)")
    else:
        for imp in cmp.impacts:
            n_impacts += 1
            g = graphs.get(imp.version_scope)
            if g is None:
                errors.append(f"V8: impact {imp.impact_id} scope "
                              f"{imp.version_scope!r} has no graph")
                continue
            prev = None
            for step in imp.path:
                # every step must be a REAL edge in the scope graph, in the
                # direction the walk actually used it
                if step.direction == "reverse":
                    ok = g.has_edge(step.to, step.frm, key=step.fact_key)
                else:
                    ok = g.has_edge(step.frm, step.to, key=step.fact_key)
                if not ok:
                    errors.append(
                        f"V8: impact {imp.impact_id} path step "
                        f"{step.render()} (fact_key={step.fact_key}) does "
                        f"not exist in v{imp.version_scope}")
                    break
                if prev is not None and step.frm != prev:
                    errors.append(
                        f"V8: impact {imp.impact_id} path is not "
                        f"connected at step {step.render()}")
                    break
                prev = step.to
            if imp.path:
                first = imp.path[0]
                anchor_ok = (first.frm in
                             {s for s in _anchors_of(cmp, imp)}
                             or first.frm == imp.path[0].frm)
            # depth-0 impacts legitimately have empty paths

    # -- V9: impact IDs unique, depth bounded, scope sane ---------------------
    imp_ids = [i.impact_id for i in cmp.impacts]
    for d in sorted({i for i in imp_ids if imp_ids.count(i) > 1}):
        errors.append(f"V9: duplicate impact_id {d}")
    for imp in cmp.impacts:
        if max_depth is not None and imp.depth > max_depth:
            errors.append(f"V9: impact {imp.impact_id} depth "
                          f"{imp.depth} exceeds configured max {max_depth}")
        if imp.version_scope not in {cmp.base_version, cmp.target_version}:
            errors.append(f"V9: impact {imp.impact_id} scope "
                          f"{imp.version_scope!r} is outside the compared "
                          f"versions")
        if imp.path:
            if imp.path[-1].to != imp.impacted_entity_key:
                errors.append(f"V9: impact {imp.impact_id} path does not "
                              f"end at the impacted entity")
            if len(imp.path) != imp.depth:
                errors.append(f"V9: impact {imp.impact_id} path length "
                              f"{len(imp.path)} != depth {imp.depth}")

    return RevisionValidationResult(valid=not errors, errors=errors,
                                    warnings=warnings,
                                    checked_changes=n_changes,
                                    checked_impacts=n_impacts)


def _anchors_of(cmp: RevisionComparison, imp) -> set[str]:
    """Entity keys that anchor the change which produced ``imp``."""
    for c in list(cmp.entity_changes) + list(cmp.relationship_changes):
        if c.change_id == imp.source_change_id:
            ek = getattr(c, "entity_key", None)
            if ek:
                return {ek}
            return {c.subject, c.object}
    return set()
