"""M7 tests: models, entity diff, relationship diff (synthetic snapshots).

All unit tests here run on hand-built VersionSnapshot fixtures (plain
namespace rows + tiny NetworkX graphs) — no database, fully deterministic.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from backend.diff.context import VersionSnapshot, canonical_key
from backend.diff.entity_diff import diff_entities
from backend.diff.models import (ChangeType, ImpactCategory, PathStep,
                                 ProvenanceSnapshot, RelationshipChange,
                                 RevisionComparison, RevisionSummary,
                                 deterministic_change_id, deterministic_impact_id,
                                 triple_of)
from backend.diff.relationship_diff import diff_relationships


# ------------------------------------------------------------ helpers ----

def ent(key, name="n", section="3.1", page=4, conf=0.9):
    return NS(entity_id=key.split(":", 1)[1], name=name, section=section,
              page=page, confidence=conf, source="deterministic")


def fact(fk, conf=0.9):
    subject, predicate, rest = fk.split("|", 2)
    obj, _, value = rest.partition("|")
    return NS(fact_key=fk, subject=subject, predicate=predicate, object=obj,
              object_value=value, confidence=conf, extractor="deterministic",
              document_name="doc.pdf", version_label="x", section_no="4.1",
              section_title="t", page_start=7, page_end=7,
              source_chunk_id="chunk")


def snap(label, entities: dict, facts: dict | None = None,
         graph=None) -> VersionSnapshot:
    facts = facts or {}
    for f in facts.values():
        f.version_label = label   # registry rows carry their true version
    triples: dict = {}
    for fk, f in facts.items():
        triples.setdefault(triple_of(fk), []).append(f)
    return VersionSnapshot(version_id=1, version_label=label,
                           document_name="doc.pdf", entities=entities,
                           facts=facts, triples=triples, graph=graph)


# ------------------------------------------------------------- models ----

class TestModels:

    def test_change_id_deterministic(self):
        a = deterministic_change_id("1.0.0", "1.1.0", "entity_removed",
                                    "component:C-05")
        b = deterministic_change_id("1.0.0", "1.1.0", "entity_removed",
                                    "component:C-05")
        assert a == b and a.startswith("M7-ENT-REM-")

    def test_change_id_varies_by_identity_and_type(self):
        base = deterministic_change_id("1.0.0", "1.1.0", "entity_removed",
                                       "component:C-05")
        assert base != deterministic_change_id("1.0.0", "1.1.0",
                                               "entity_added",
                                               "component:C-05")
        assert base != deterministic_change_id("1.0.0", "1.1.0",
                                               "entity_removed",
                                               "component:C-06")

    def test_triple_of_pads(self):
        assert triple_of("a|provides|b|") == ("a", "provides", "b", "")
        assert triple_of("a|carries|signal:SG-1") == \
            ("a", "carries", "signal:SG-1", "")

    def test_impact_id_deterministic(self):
        assert (deterministic_impact_id("M7-X", "component:C-1", 1) ==
                deterministic_impact_id("M7-X", "component:C-1", 1))

    def test_entity_change_confidence_range(self):
        from backend.diff.models import EntityChange
        with pytest.raises(Exception):
            EntityChange(change_type=ChangeType.ENTITY_ADDED,
                         entity_key="component:C-1", entity_type="component",
                         display_name="x", base_version="1.0.0",
                         target_version="1.1.0", confidence=1.5)

    def test_provenance_snapshot_pages_csv(self):
        p = ProvenanceSnapshot(document_name="d", version_label="1.0.0",
                               page_start=9, page_end=9, source_chunk_id="c")
        assert p.to_dict()["pages_csv"] == "9"
        p2 = ProvenanceSnapshot(document_name="d", version_label="1.0.0",
                                page_start=9, page_end=11, source_chunk_id="c")
        assert p2.to_dict()["pages_csv"] == "9-11"

    def test_pathstep_reverse_render(self):
        fwd = PathStep(frm="a", predicate="requires", to="b", fact_key="k")
        rev = PathStep(frm="b", predicate="requires", to="a", fact_key="k",
                       direction="reverse")
        assert "--requires-->" in fwd.render()
        assert "<--requires--" in rev.render()

    def test_summary_counts(self):
        s = RevisionSummary(entity_removed=3, relationship_added=2,
                            impacts_by_category={"direct": 5})
        assert s.to_dict()["entity_removed"] == 3

    def test_comparison_roundtrip(self):
        c = RevisionComparison(base_version="1.0.0", target_version="1.1.0",
                               summary=RevisionSummary(entity_removed=1))
        assert c.to_dict()["base_version"] == "1.0.0"
        assert "entity_removed" in c.to_json()


# --------------------------------------------------------- entity diff ----

class TestEntityDiff:

    def test_added_removed_changed(self):
        b = snap("1.0.0", {"component:C-01": ent("component:C-01", "A"),
                           "component:C-05": ent("component:C-05", "Old"),
                           "interface:IF-08": ent("interface:IF-08", "S")})
        t = snap("1.1.0", {"component:C-01": ent("component:C-01", "A"),
                           "component:C-05": ent("component:C-05", "New"),
                           "component:C-21": ent("component:C-21", "Z")})
        changes = diff_entities(b, t)
        kinds = {(c.change_type.value, c.entity_key) for c in changes}
        assert ("entity_added", "component:C-21") in kinds
        assert ("entity_changed", "component:C-05") in kinds
        assert ("entity_removed", "interface:IF-08") in kinds
        # unchanged key must NOT appear
        assert ("entity_added", "component:C-01") not in kinds
        assert ("entity_removed", "component:C-01") not in kinds

    def test_removed_entity_uses_base_provenance(self):
        b = snap("1.0.0", {"component:C-05": ent("component:C-05", "Gone",
                                                 page=6)})
        t = snap("1.1.0", {})
        changes = diff_entities(b, t)
        assert len(changes) == 1
        c = changes[0]
        assert c.change_type == ChangeType.ENTITY_REMOVED
        assert c.provenance[0].version_label == "1.0.0"
        assert c.provenance[0].page_start == 6
        assert c.display_name == "Gone"

    def test_added_entity_uses_target_provenance(self):
        b = snap("1.0.0", {})
        t = snap("1.1.0", {"signal:SG-099": ent("signal:SG-099", "New",
                                                page=12)})
        c = diff_entities(b, t)[0]
        assert c.change_type == ChangeType.ENTITY_ADDED
        assert c.provenance[0].version_label == "1.1.0"
        assert c.entity_type == "signal"

    def test_changed_carries_both_names_and_both_provenance(self):
        b = snap("1.0.0", {"component:C-09": ent("component:C-09", "Old")})
        t = snap("1.1.0", {"component:C-09": ent("component:C-09", "New")})
        c = diff_entities(b, t)[0]
        assert c.base_name == "Old" and c.target_name == "New"
        assert {p.version_label for p in c.provenance} == {"1.0.0", "1.1.0"}

    def test_deterministic_ordering(self):
        b = snap("1.0.0", {f"component:C-{i:03d}": ent(f"component:C-{i:03d}")
                           for i in (3, 1, 2)})
        t = snap("1.1.0", {})
        keys = [c.entity_key for c in diff_entities(b, t)]
        assert keys == sorted(keys)

    def test_identical_snapshots_produce_no_changes(self):
        e = {"component:C-01": ent("component:C-01", "A")}
        assert diff_entities(snap("1.0.0", dict(e)),
                             snap("1.0.0", dict(e))) == []


# ---------------------------------------------------- relationship diff ----

class TestRelationshipDiff:

    def test_added_removed(self):
        b = snap("1.0.0", {}, {"component:C-02|provides|interface:IF-03|":
                               fact("component:C-02|provides|"
                                    "interface:IF-03|")})
        t = snap("1.1.0", {}, {"component:C-04|requires|interface:IF-03|":
                               fact("component:C-04|requires|"
                                    "interface:IF-03|")})
        changes = diff_relationships(b, t)
        kinds = {(c.change_type.value, c.subject, c.predicate, c.object)
                 for c in changes}
        assert ("relationship_added", "component:C-04", "requires",
                "interface:IF-03") in kinds
        assert ("relationship_removed", "component:C-02", "provides",
                "interface:IF-03") in kinds

    def test_value_change_manifests_as_remove_add(self):
        """fact_key = subject|predicate|object|object_value, so the
        object_value participates in semantic identity (D-037). A fact
        whose value segment changed is a DIFFERENT triple and must
        manifest as REMOVED old + ADDED new (task rule 6) — never as a
        synthetic 'changed' relationship."""
        b = snap("1.0.0", {}, {"component:C-01|requires|interface:IF-05|":
                               fact("component:C-01|requires|"
                                    "interface:IF-05|")})
        t = snap("1.1.0", {}, {"component:C-01|requires|interface:IF-05|v2":
                               fact("component:C-01|requires|"
                                    "interface:IF-05|v2")})
        changes = diff_relationships(b, t)
        kinds = {c.change_type for c in changes}
        assert kinds == {ChangeType.RELATIONSHIP_REMOVED,
                         ChangeType.RELATIONSHIP_ADDED}

    def test_unchanged_triple_is_neither_added_nor_removed(self):
        fk = "component:C-01|requires|interface:IF-05|"
        b = snap("1.0.0", {}, {fk: fact(fk)})
        t = snap("1.1.0", {}, {fk: fact(fk)})
        assert diff_relationships(b, t) == []
        b = snap("1.0.0", {}, {"a|provides|b|": fact("a|provides|b|")})
        t = snap("1.1.0", {}, {"c|requires|b|": fact("c|requires|b|")})
        changes = diff_relationships(b, t)
        by_type = {c.change_type: c for c in changes}
        assert (by_type[ChangeType.RELATIONSHIP_REMOVED].provenance[0]
                .version_label == "1.0.0")
        assert (by_type[ChangeType.RELATIONSHIP_ADDED].provenance[0]
                .version_label == "1.1.0")

    def test_confidence_is_max_over_parallel_rows(self):
        fk = "component:C-01|requires|interface:IF-05|"
        b = snap("1.0.0", {}, {fk: fact(fk, conf=0.5)})
        t = snap("1.1.0", {}, {fk: fact(fk, conf=0.5),
                               "component:C-02|requires|interface:IF-06|":
                               fact("component:C-02|requires|"
                                    "interface:IF-06|", conf=0.8)})
        changes = diff_relationships(b, t)
        assert len(changes) == 1  # only the added IF-06 fact
        assert changes[0].confidence == 0.8

    def test_deterministic_ordering_by_triple(self):
        b = snap("1.0.0", {}, {f"component:C-{i:02d}|depends_on|"
                               f"component:C-99|":
                               fact(f"component:C-{i:02d}|depends_on|"
                                    f"component:C-99|")
                               for i in (5, 1, 3)})
        t = snap("1.1.0", {}, {})
        triples = [c.triple for c in diff_relationships(b, t)]
        assert triples == sorted(triples)


# ------------------------------------------------------------- context ----

class TestCanonicalKey:

    def test_canonical_key_normalization(self):
        assert canonical_key("COMPONENT:c-05") == "component:C-05"
        assert canonical_key("component:C-05") == "component:C-05"
        assert canonical_key("no-colon") == "no-colon"
