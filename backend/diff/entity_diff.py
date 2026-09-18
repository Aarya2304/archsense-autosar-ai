"""Entity diff (M7.4): canonical-key set comparison between two snapshots.

Identity is the M4 canonical key, so an ordinary reference to an entity is
never mistaken for a new entity (task rule 4). A key present in both sides
with a changed display name is ENTITY_CHANGED (rename kept stable identity
— the only attribute-level change the registry actually supports; see
models.ChangeType).
"""

from __future__ import annotations

from backend.diff.context import VersionSnapshot
from backend.diff.models import (ChangeType, EntityChange, ProvenanceSnapshot,
                                 deterministic_change_id)
from backend.findings.context import provenance_of_entity


def _entity_provenance(key: str, snap: VersionSnapshot) -> ProvenanceSnapshot:
    """Trusted provenance for one entity from its typed registry row.

    The typed rows store page/section but not document/version; the
    snapshot supplies those (they are version-scoped by construction).
    """
    row = snap.entities[key]
    base = provenance_of_entity(row)
    return ProvenanceSnapshot(
        document_name=snap.document_name,
        version_label=snap.version_label,
        section_no=base.get("section_no", ""),
        section_title="",
        page_start=base.get("page_start", 0),
        page_end=base.get("page_end", 0),
        source_chunk_id="")


def diff_entities(base: VersionSnapshot,
                  target: VersionSnapshot) -> list[EntityChange]:
    """Deterministic entity diff, sorted by (change_type, type, key)."""
    out: list[EntityChange] = []

    for key in sorted(target.entity_keys - base.entity_keys):
        row = target.entities[key]
        etype = key.split(":", 1)[0]
        out.append(EntityChange(
            change_id=deterministic_change_id(
                base.version_label, target.version_label,
                ChangeType.ENTITY_ADDED, key),
            change_type=ChangeType.ENTITY_ADDED,
            entity_key=key, entity_type=etype,
            display_name=getattr(row, "name", "") or key.split(":", 1)[1],
            base_version=base.version_label,
            target_version=target.version_label,
            confidence=float(getattr(row, "confidence", 0.0) or 0.0),
            provenance=[_entity_provenance(key, target)],
            metadata={"extractor": getattr(row, "source", "")}))

    for key in sorted(base.entity_keys - target.entity_keys):
        row = base.entities[key]
        etype = key.split(":", 1)[0]
        out.append(EntityChange(
            change_id=deterministic_change_id(
                base.version_label, target.version_label,
                ChangeType.ENTITY_REMOVED, key),
            change_type=ChangeType.ENTITY_REMOVED,
            entity_key=key, entity_type=etype,
            display_name=getattr(row, "name", "") or key.split(":", 1)[1],
            base_version=base.version_label,
            target_version=target.version_label,
            confidence=float(getattr(row, "confidence", 0.0) or 0.0),
            provenance=[_entity_provenance(key, base)],
            metadata={"extractor": getattr(row, "source", "")}))

    # ENTITY_CHANGED: identity stable, display name changed (rename)
    for key in sorted(base.entity_keys & target.entity_keys):
        b, t = base.entities[key], target.entities[key]
        bn = getattr(b, "name", "") or ""
        tn = getattr(t, "name", "") or ""
        if bn and tn and bn != tn:
            out.append(EntityChange(
                change_id=deterministic_change_id(
                    base.version_label, target.version_label,
                    ChangeType.ENTITY_CHANGED, key),
                change_type=ChangeType.ENTITY_CHANGED,
                entity_key=key,
                entity_type=key.split(":", 1)[0],
                display_name=tn,
                base_version=base.version_label,
                target_version=target.version_label,
                base_name=bn, target_name=tn,
                confidence=float(getattr(t, "confidence", 0.0) or 0.0),
                provenance=[_entity_provenance(key, base),
                            _entity_provenance(key, target)],
                metadata={"extractor": getattr(t, "source", "")}))

    out.sort(key=lambda c: c.sort_key())
    return out
