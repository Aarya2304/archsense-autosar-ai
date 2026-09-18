"""M7 tests: impact analysis, validation, comparator end-to-end, persistence.

Impact tests use tiny hand-built graphs (deterministic, no DB). Comparator,
persistence and evaluation tests run on an isolated per-module SQLite DB
with both HLD versions extracted (same pattern as the M6 tests).
"""

from __future__ import annotations

import json

import networkx as nx
import pytest

from backend.diff.comparator import RevisionComparator
from backend.diff.context import VersionSnapshot, load_both_snapshots
from backend.diff.evaluation import evaluate_revisions
from backend.diff.impact import analyze_impacts
from backend.diff.models import (ChangeType, EntityChange, ImpactCategory,
                                 PathStep, RelationshipChange,
                                 deterministic_impact_id)
from backend.diff.persistence import (clear_comparison, load_comparison,
                                      persist_comparison)
from backend.diff.validation import validate_comparison
from backend.extraction.service import ExtractionService
from backend.rag.chunker import chunk_processed_json
from backend.storage.database import (init_schema, make_engine,
                                      make_session_factory)


# ------------------------------------------------------------ helpers ----

def _graph(edges: dict[str, list[tuple[str, str, str]]]) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for u, targets in edges.items():
        g.add_node(u)
        for (v, pred, fk) in targets:
            g.add_node(v)
            g.add_edge(u, v, key=fk, predicate=pred, fact_key=fk)
    return g


def _snap(label, graph) -> VersionSnapshot:
    return VersionSnapshot(version_id=1, version_label=label,
                           document_name="doc.pdf", entities={}, facts={},
                           triples={}, graph=graph)


def _ent_change(ctype, key, cid="M7-TEST-1"):
    return EntityChange(change_id=cid, change_type=ctype, entity_key=key,
                        entity_type=key.split(":", 1)[0], display_name=key,
                        base_version="1.0.0", target_version="1.1.0",
                        provenance=[])


# ------------------------------------------------------------- impact ----

class TestImpact:

    def test_direct_and_provider_via_provides(self):
        # C-01 --provides--> IF-03; C-08 requires IF-03 (reverse hop)
        g = _graph({"component:C-01": [("interface:IF-03", "provides",
                                        "k1")],
                    "component:C-08": [("interface:IF-03", "requires",
                                        "k2")]})
        base = _snap("1.0.0", g)
        ch = _ent_change(ChangeType.ENTITY_REMOVED, "component:C-01")
        imps = analyze_impacts([ch], [], base, base, max_depth=1)
        by_key = {i.impacted_entity_key: i for i in imps}
        assert by_key["component:C-01"].category == ImpactCategory.DIRECT
        assert by_key["component:C-01"].depth == 0
        assert by_key["interface:IF-03"].category == \
            ImpactCategory.INTERFACE_PROVIDER
        assert by_key["interface:IF-03"].depth == 1

    def test_consumer_via_requires(self):
        g = _graph({"component:C-08": [("interface:IF-03", "requires",
                                        "k2")]})
        base = _snap("1.0.0", g)
        ch = _ent_change(ChangeType.ENTITY_REMOVED, "interface:IF-03")
        imps = analyze_impacts([ch], [], base, base, max_depth=1)
        cat = {i.impacted_entity_key: i.category for i in imps}
        assert cat["component:C-08"] == ImpactCategory.INTERFACE_CONSUMER

    def test_dependency_and_signal_and_flow_categories(self):
        g = _graph({
            "component:C-02": [("component:C-09", "depends_on", "k1")],
            "interface:IF-01": [("signal:SG-01", "carries", "k2")],
            "component:C-03": [("functional_flow:FL-1", "participates_in",
                                "k3")]})
        base = _snap("1.0.0", g)
        imps = analyze_impacts(
            [_ent_change(ChangeType.ENTITY_REMOVED, "component:C-02",
                         "c1"),
             _ent_change(ChangeType.ENTITY_REMOVED, "interface:IF-01",
                         "c2"),
             _ent_change(ChangeType.ENTITY_REMOVED, "component:C-03",
                         "c3")], [], base, base, max_depth=1)
        cat = {i.impacted_entity_key: i.category for i in imps}
        assert cat["component:C-09"] == ImpactCategory.DEPENDENCY
        assert cat["signal:SG-01"] == ImpactCategory.SIGNAL
        assert cat["functional_flow:FL-1"] == \
            ImpactCategory.FUNCTIONAL_FLOW

    def test_depth1_stops_at_one_hop(self):
        g = _graph({"component:C-01": [("interface:IF-03", "provides",
                                        "k1")],
                    "component:C-08": [("interface:IF-03", "requires",
                                        "k2")]})
        base = _snap("1.0.0", g)
        ch = _ent_change(ChangeType.ENTITY_REMOVED, "component:C-01")
        imps = analyze_impacts([ch], [], base, base, max_depth=1)
        assert max(i.depth for i in imps) == 1
        assert "component:C-08" not in {i.impacted_entity_key for i in imps}

    def test_depth2_reaches_transitive_hop_with_real_path(self):
        g = _graph({"component:C-01": [("interface:IF-03", "provides",
                                        "k1")],
                    "component:C-08": [("interface:IF-03", "requires",
                                        "k2")]})
        base = _snap("1.0.0", g)
        ch = _ent_change(ChangeType.ENTITY_REMOVED, "component:C-01")
        imps = analyze_impacts([ch], [], base, base, max_depth=2)
        c08 = [i for i in imps if i.impacted_entity_key == "component:C-08"]
        assert len(c08) == 1
        item = c08[0]
        assert item.depth == 2 and item.category == ImpactCategory.TRANSITIVE
        # path: two real, connected, walk-ordered steps
        assert len(item.path) == 2
        assert item.path[0].frm == "component:C-01"
        assert item.path[1].frm == "interface:IF-03"
        assert item.path[-1].to == "component:C-08"

    def test_reverse_edge_paths_are_connected(self):
        # in-edge: IF-03 requires-edge points AT the anchor
        g = _graph({"component:C-08": [("interface:IF-05", "requires",
                                        "k9")]})
        base = _snap("1.0.0", g)
        ch = _ent_change(ChangeType.ENTITY_REMOVED, "interface:IF-05")
        imps = analyze_impacts([ch], [], base, base, max_depth=2)
        c08 = [i for i in imps if i.impacted_entity_key == "component:C-08"
               and i.depth == 1][0]
        step = c08.path[0]
        assert step.frm == "interface:IF-05" and step.to == "component:C-08"
        assert step.direction == "reverse"
        # and the validator accepts the walk-ordered path
        ch2 = _ent_change(ChangeType.ENTITY_REMOVED, "interface:IF-05")
        ch2.provenance = []
        res = validate_comparison(
            _cmp_with_imps([ch2], imps), graphs={"1.0.0": g})
        assert not any(e.startswith("V8") for e in res.errors), res.errors

    def test_relationship_change_anchors_both_endpoints(self):
        g = _graph({"component:C-19": [("component:C-11", "depends_on",
                                        "k1")]})
        base = _snap("1.0.0", g)
        target = _snap("1.1.0", _graph({}))
        rel = RelationshipChange(
            change_id="M7-TEST-R1",
            change_type=ChangeType.RELATIONSHIP_REMOVED,
            fact_key="component:C-19|depends_on|component:C-11|",
            subject="component:C-19", predicate="depends_on",
            object="component:C-11", base_version="1.0.0",
            target_version="1.1.0", provenance=[])
        imps = analyze_impacts([], [rel], base, target, max_depth=1)
        keys = {i.impacted_entity_key for i in imps}
        assert {"component:C-19", "component:C-11"} <= keys

    def test_deterministic_output(self):
        g = _graph({f"component:C-{i}": [("interface:IF-1", "requires",
                                          f"k{i}")]
                     for i in range(6)})
        base = _snap("1.0.0", g)
        ch = _ent_change(ChangeType.ENTITY_REMOVED, "interface:IF-1")
        a = analyze_impacts([ch], [], base, base, max_depth=1)
        b = analyze_impacts([ch], [], base, base, max_depth=1)
        assert [i.sort_key() for i in a] == [i.sort_key() for i in b]

    def test_impact_ids_unique_per_change_entity(self):
        g = _graph({"component:C-08": [("interface:IF-05", "requires",
                                        "k9")]})
        base = _snap("1.0.0", g)
        ch = _ent_change(ChangeType.ENTITY_REMOVED, "interface:IF-05")
        imps = analyze_impacts([ch], [], base, base, max_depth=1)
        ids = [i.impact_id for i in imps]
        assert len(ids) == len(set(ids))


def _cmp_with_imps(changes, imps):
    from backend.diff.models import RevisionComparison, RevisionSummary
    return RevisionComparison(
        base_version="1.0.0", target_version="1.1.0",
        entity_changes=[c for c in changes], relationship_changes=[],
        impacts=imps, summary=RevisionSummary())


# ---------------------------------------------------------- validation ----

class TestValidation:

    def _base_cmp(self):
        from backend.diff.models import (RevisionComparison,
                                         RevisionSummary)
        return RevisionComparison(base_version="1.0.0",
                                  target_version="1.1.0",
                                  summary=RevisionSummary())

    def test_same_version_rejected(self):
        cmp = self._base_cmp()
        cmp.target_version = "1.0.0"
        res = validate_comparison(cmp)
        assert not res.valid and any(e.startswith("V1") for e in res.errors)

    def test_missing_version_rejected(self):
        cmp = self._base_cmp()
        cmp.base_version = ""
        res = validate_comparison(cmp)
        assert not res.valid and any(e.startswith("V1") for e in res.errors)

    def test_invalid_change_type(self):
        cmp = self._base_cmp()
        c = _ent_change(ChangeType.ENTITY_ADDED, "component:C-01")
        c.change_type = "bogus_type"
        cmp.entity_changes = [c]
        res = validate_comparison(cmp)
        assert any(e.startswith("V2") for e in res.errors)

    def test_duplicate_change_ids(self):
        cmp = self._base_cmp()
        a = _ent_change(ChangeType.ENTITY_REMOVED, "component:C-01",
                        "M7-DUP")
        b = _ent_change(ChangeType.ENTITY_REMOVED, "component:C-02",
                        "M7-DUP")
        cmp.entity_changes = [a, b]
        res = validate_comparison(cmp)
        assert any(e.startswith("V3") for e in res.errors)

    def test_non_canonical_entity_key(self):
        cmp = self._base_cmp()
        cmp.entity_changes = [_ent_change(ChangeType.ENTITY_REMOVED,
                                          "C-01-noPrefix")]
        res = validate_comparison(cmp)
        assert any(e.startswith("V4") for e in res.errors)

    def test_empty_relationship_triple(self):
        cmp = self._base_cmp()
        cmp.relationship_changes = [RelationshipChange(
            change_id="M7-X", change_type=ChangeType.RELATIONSHIP_ADDED,
            fact_key="k", subject="", predicate="provides",
            object="interface:IF-1", base_version="1.0.0",
            target_version="1.1.0")]
        res = validate_comparison(cmp)
        assert any(e.startswith("V5") for e in res.errors)

    def test_provenance_version_mismatch(self):
        cmp = self._base_cmp()
        c = _ent_change(ChangeType.ENTITY_REMOVED, "component:C-01")
        from backend.diff.models import ProvenanceSnapshot
        c.provenance = [ProvenanceSnapshot(version_label="1.1.0")]  # wrong side
        cmp.entity_changes = [c]
        res = validate_comparison(cmp)
        assert any(e.startswith("V6") for e in res.errors)

    def test_fabricated_impact_path_rejected(self):
        g = _graph({"component:C-01": [("interface:IF-03", "provides",
                                        "k1")]})
        cmp = self._base_cmp()
        imp = _imp("M7-IMP-FAKE", "interface:IF-03", 1, "1.0.0",
                   [PathStep(frm="component:C-01", predicate="provides",
                             to="interface:IF-99", fact_key="k1")])
        cmp.impacts = [imp]
        res = validate_comparison(cmp, graphs={"1.0.0": g})
        assert any(e.startswith("V8") for e in res.errors)

    def test_real_impact_path_accepted(self):
        g = _graph({"component:C-01": [("interface:IF-03", "provides",
                                        "k1")]})
        cmp = self._base_cmp()
        imp = _imp("M7-IMP-REAL", "interface:IF-03", 1, "1.0.0",
                   [PathStep(frm="component:C-01", predicate="provides",
                             to="interface:IF-03", fact_key="k1")])
        cmp.impacts = [imp]
        res = validate_comparison(cmp, graphs={"1.0.0": g})
        assert res.valid, res.errors

    def test_duplicate_impact_ids(self):
        cmp = self._base_cmp()
        cmp.impacts = [_imp("M7-IMP-DUP", "component:C-01", 0, "1.0.0", []),
                       _imp("M7-IMP-DUP", "component:C-02", 0, "1.0.0", [])]
        res = validate_comparison(cmp)
        assert any(e.startswith("V9") for e in res.errors)

    def test_impact_depth_bound(self):
        cmp = self._base_cmp()
        cmp.impacts = [_imp("M7-IMP-D9", "component:C-01", 9, "1.0.0", [])]
        res = validate_comparison(cmp, max_depth=1)
        assert any(e.startswith("V9") for e in res.errors)


def _imp(iid, key, depth, scope, path):
    from backend.diff.models import ImpactItem
    return ImpactItem(impact_id=iid, source_change_id="M7-TEST-1",
                      impacted_entity_key=key, entity_type="component",
                      category=ImpactCategory.DIRECT, reason="t",
                      path=path, depth=depth, version_scope=scope)


# ------------------------------------------ comparator end-to-end (DB) ----

@pytest.fixture(scope="module")
def registry_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("diffreg") / "registry.db"
    svc = ExtractionService(db_path=db)
    for ver in ("1.0.0", "1.1.0"):
        chunks = chunk_processed_json(
            f"data/processed/ABC_HLD_v{ver}__processed.json")
        res = svc.extract_from_chunks(chunks, document_name=f"d{ver}.pdf",
                                      version=ver, persist=True)
        assert res.status == "completed", res.issues
    return db


@pytest.fixture(scope="module")
def sf(registry_db):
    engine = make_engine(registry_db)
    init_schema(engine)
    return make_session_factory(engine)


class TestComparatorEndToEnd:

    def test_gold_matching_counts(self, sf):
        s = sf()
        base, target = load_both_snapshots(s, "1.0.0", "1.1.0")
        s.close()
        cmp = RevisionComparator(session_factory=sf).compare(
            "1.0.0", "1.1.0", depth=1)
        assert cmp.summary.entity_added == 0
        assert cmp.summary.entity_removed == 16
        assert cmp.summary.entity_changed == 1
        assert cmp.summary.relationship_added == 43
        assert cmp.summary.relationship_removed == 65
        assert cmp.validation["valid"]

    def test_d1_removed_dependency_witness(self, sf):
        cmp = RevisionComparator(session_factory=sf).compare(
            "1.0.0", "1.1.0", depth=1)
        removed = {(c.subject, c.predicate, c.object)
                   for c in cmp.relationship_changes
                   if c.change_type == ChangeType.RELATIONSHIP_REMOVED}
        assert ("component:C-19", "depends_on",
                "component:C-11") in removed

    def test_d8_new_consumer_witness(self, sf):
        cmp = RevisionComparator(session_factory=sf).compare(
            "1.0.0", "1.1.0", depth=1)
        added = {(c.subject, c.predicate, c.object)
                 for c in cmp.relationship_changes
                 if c.change_type == ChangeType.RELATIONSHIP_ADDED}
        assert ("component:C-04", "requires", "interface:IF-03") in added

    def test_c09_rename_entity_changed(self, sf):
        cmp = RevisionComparator(session_factory=sf).compare(
            "1.0.0", "1.1.0", depth=1)
        chg = [c for c in cmp.entity_changes
               if c.entity_key == "component:C-09"]
        assert len(chg) == 1
        assert chg[0].change_type == ChangeType.ENTITY_CHANGED
        assert chg[0].base_name == "VehicleModeSWC"

    def test_deterministic_repeated_execution(self, sf):
        a = RevisionComparator(session_factory=sf).compare(
            "1.0.0", "1.1.0", depth=1).to_dict()
        b = RevisionComparator(session_factory=sf).compare(
            "1.0.0", "1.1.0", depth=1).to_dict()
        a.pop("timings_ms"), b.pop("timings_ms")
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    def test_same_version_rejected(self, sf):
        with pytest.raises(ValueError, match="identical"):
            RevisionComparator(session_factory=sf).compare("1.0.0", "1.0.0")

    def test_unknown_version_rejected(self, sf):
        with pytest.raises(ValueError, match="unknown version"):
            RevisionComparator(session_factory=sf).compare("9.9.9", "1.1.0")

    def test_version_isolation(self, sf):
        s = sf()
        base, target = load_both_snapshots(s, "1.0.0", "1.1.0")
        s.close()
        # v2-only facts must never appear in the base snapshot
        v2_only_triple = ("component:C-04", "requires", "interface:IF-03", "")
        assert v2_only_triple not in base.triple_set
        assert v2_only_triple in target.triple_set
        cmp = RevisionComparator(session_factory=sf).compare(
            "1.0.0", "1.1.0", depth=1)
        # every change side references only its own version
        for c in cmp.entity_changes + cmp.relationship_changes:
            if c.change_type.value.endswith("_added"):
                assert c.target_version == "1.1.0"
            if c.change_type.value.endswith("_removed"):
                assert c.base_version == "1.0.0"
        # every impact lives in exactly one compared version's graph
        for i in cmp.impacts:
            assert i.version_scope in ("1.0.0", "1.1.0")

    def test_depth2_impacts_and_validation(self, sf):
        cmp = RevisionComparator(session_factory=sf).compare(
            "1.0.0", "1.1.0", depth=2)
        assert cmp.summary.impact_count > cmp.summary.impact_count // 2
        assert cmp.validation["valid"]
        assert max(i.depth for i in cmp.impacts) == 2


class TestPersistence:

    def test_persist_load_clear_idempotent(self, sf):
        s = sf()
        cmp = RevisionComparator(session_factory=sf).compare(
            "1.0.0", "1.1.0", depth=1)
        first = persist_comparison(s, cmp, depth=1)
        assert first["created"] is True
        second = persist_comparison(s, cmp, depth=1)
        assert second["created"] is False
        assert second["compare_run_id"] == first["compare_run_id"]
        loaded = load_comparison(s, "1.0.0", "1.1.0")
        assert loaded is not None
        assert loaded["comparison"]["summary"]["entity_removed"] == 16
        assert loaded["params"]["depth"] == 1
        cleared = clear_comparison(s, "1.0.0", "1.1.0")
        assert cleared == 1
        assert load_comparison(s, "1.0.0", "1.1.0") is None
        s.close()


# ---------------------------------------------------------- evaluation ----

class TestEvaluation:

    def test_all_applicable_families_perfect(self, sf):
        s = sf()
        ev = evaluate_revisions(s, "1.0.0", "1.1.0", depth=1).to_dict()
        s.close()
        for fam in ("entity_removed", "entity_changed",
                    "relationship_added", "relationship_removed", "impact"):
            assert ev[fam]["f1"] == 1.0, (fam, ev[fam])
        assert ev["stale_reference"]["expected"] == 0

    def test_defect_verdicts(self, sf):
        s = sf()
        ev = evaluate_revisions(s, "1.0.0", "1.1.0", depth=1).to_dict()
        s.close()
        by_id = {d["defect_id"].split("_")[0]: d for d in ev["defects"]}
        assert by_id["D1"]["status"] == "applicable"
        assert by_id["D1"]["detected"] is True
        assert by_id["D8"]["status"] == "applicable"
        assert by_id["D8"]["detected"] is True
        for did in ("D2", "D3", "D5", "D6", "D7"):
            assert by_id[did]["status"] == "not_applicable"
            assert by_id[did]["reason"]

    def test_gold_consistency_reported(self, sf):
        s = sf()
        ev = evaluate_revisions(s, "1.0.0", "1.1.0", depth=1).to_dict()
        s.close()
        gc = ev["gold_consistency"]
        assert gc["added_matches_expected_diff"] is True
        assert gc["removed_superset_of_expected_diff"] is True
        assert len(gc["expected_diff_missing_ports_removed"]) == 5

    def test_json_serializable(self, sf):
        s = sf()
        ev = evaluate_revisions(s, "1.0.0", "1.1.0", depth=1).to_dict()
        s.close()
        assert json.dumps(ev)
