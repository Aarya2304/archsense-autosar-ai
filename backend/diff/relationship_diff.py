"""Relationship diff (M7.5): fact-triple set comparison between snapshots.

Identity across versions is the fact TRIPLE (subject, predicate, object,
object_value) — see D-037. Within one version, M4 dedupe guarantees one
fact_key per triple; across versions a semantic change produces a new
fact_key and therefore surfaces naturally as REMOVED + ADDED (task rule 6).
Confidence for a shared triple is the max over its fact rows on each side;
the report keeps the representative (lowest-sorted) fact_key per side.
"""

from __future__ import annotations

from backend.diff.context import VersionSnapshot
from backend.diff.models import (ChangeType, ProvenanceSnapshot,
                                 RelationshipChange,
                                 deterministic_change_id)


def _representative_fact(snap: VersionSnapshot, triple) -> object:
    rows = snap.triples[triple]
    return sorted(rows, key=lambda f: f.fact_key)[0]


def _rel_provenance(snap: VersionSnapshot, triple) -> ProvenanceSnapshot:
    return ProvenanceSnapshot.from_fact(_representative_fact(snap, triple))


def diff_relationships(base: VersionSnapshot,
                       target: VersionSnapshot) -> list[RelationshipChange]:
    """Deterministic relationship diff, sorted by (change_type, triple)."""
    out: list[RelationshipChange] = []
    bt, tt = base.triple_set, target.triple_set

    for triple in sorted(tt - bt):
        f = _representative_fact(target, triple)
        out.append(RelationshipChange(
            change_id=deterministic_change_id(
                base.version_label, target.version_label,
                ChangeType.RELATIONSHIP_ADDED, "|".join(triple)),
            change_type=ChangeType.RELATIONSHIP_ADDED,
            fact_key=f.fact_key,
            subject=triple[0], predicate=triple[1], object=triple[2],
            object_value=triple[3],
            base_version=base.version_label,
            target_version=target.version_label,
            confidence=float(f.confidence or 0.0),
            extractor=f.extractor,
            provenance=[_rel_provenance(target, triple)]))

    for triple in sorted(bt - tt):
        f = _representative_fact(base, triple)
        out.append(RelationshipChange(
            change_id=deterministic_change_id(
                base.version_label, target.version_label,
                ChangeType.RELATIONSHIP_REMOVED, "|".join(triple)),
            change_type=ChangeType.RELATIONSHIP_REMOVED,
            fact_key=f.fact_key,
            subject=triple[0], predicate=triple[1], object=triple[2],
            object_value=triple[3],
            base_version=base.version_label,
            target_version=target.version_label,
            confidence=float(f.confidence or 0.0),
            extractor=f.extractor,
            provenance=[_rel_provenance(base, triple)]))

    out.sort(key=lambda c: c.sort_key())
    return out
