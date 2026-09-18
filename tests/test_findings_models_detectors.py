"""M6 tests: finding model, deterministic IDs, mechanical validation, detectors.

Detector unit tests run on hand-built synthetic AnalysisContexts
(SimpleNamespace registry rows + real NetworkX graphs) so every rule fires
both positively and negatively under full control. Fast, offline, no LLM.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import networkx as nx
import pytest

from backend.findings.context import AnalysisContext, canonical_key
from backend.findings.detectors import (detect_conflicting_providers,
                                        detect_dangling_requires,
                                        detect_duplicate_interfaces,
                                        detect_orphan_entities,
                                        detect_unconsumed_signals,
                                        detect_undefined_references)
from backend.findings.models import (Finding, FindingType,
                                     deterministic_finding_id)
from backend.findings.validator import validate_findings
from backend.storage.models import FindingSeverity, FindingStatus


# ------------------------------------------------------------- helpers ----

def fact(key, subject, predicate, obj, conf=0.9, **kw):
    return NS(fact_key=key, subject=subject, predicate=predicate, object=obj,
              object_value=kw.get("object_value", ""),
              confidence=conf, document_name="doc.pdf", version_label="9.9.9",
              sha256="", section_no="1.2", section_title="T",
              page_start=3, page_end=3, source_chunk_id="ck")


def entity(key, conf=0.95, name=None):
    etype, _, eid = key.partition(":")
    return NS(entity_type=etype, entity_id=eid, name=name or eid.upper(),
              confidence=conf, section="3.1", page=4)


def make_ctx(facts, entities, graph=None, version="9.9.9"):
    provides: dict[str, set[str]] = {}
    requires: dict[str, set[str]] = {}
    carries: dict[str, list[str]] = {}
    by_subject: dict[str, list] = {}
    for f in facts:
        by_subject.setdefault(canonical_key(f.subject), []).append(f)
        if f.predicate == "provides":
            provides.setdefault(canonical_key(f.object), set()).add(
                canonical_key(f.subject))
        elif f.predicate == "requires":
            requires.setdefault(canonical_key(f.object), set()).add(
                canonical_key(f.subject))
        elif f.predicate == "carries":
            carries.setdefault(canonical_key(f.subject), []).append(
                canonical_key(f.object))
    return AnalysisContext(
        version_id=1, version_label=version, document_name="doc.pdf",
        entities=entities, facts={f.fact_key: f for f in facts},
        provides=provides, requires=requires, carries=carries,
        graph=graph if graph is not None else nx.MultiDiGraph(),
        facts_by_subject=by_subject)


# ----------------------------------------------------- model / IDs ----

class TestFindingModel:
    def test_deterministic_id_stable(self):
        a = deterministic_finding_id("1.0.0", FindingType.ORPHAN_ENTITY,
                                     ["component:C-05"], [])
        b = deterministic_finding_id("1.0.0", FindingType.ORPHAN_ENTITY,
                                     ["component:C-05"], [])
        assert a == b and a.startswith("M6-ORPHAN-")

    def test_deterministic_id_order_insensitive(self):
        a = deterministic_finding_id("1.0.0", FindingType.CONFLICTING_PROVIDERS,
                                     ["component:C-08", "component:C-10"],
                                     ["f2", "f1"])
        b = deterministic_finding_id("1.0.0", FindingType.CONFLICTING_PROVIDERS,
                                     ["component:C-10", "component:C-08"],
                                     ["f1", "f2"])
        assert a == b

    def test_deterministic_id_varies(self):
        a = deterministic_finding_id("1.0.0", FindingType.ORPHAN_ENTITY,
                                     ["component:C-05"], [])
        b = deterministic_finding_id("1.1.0", FindingType.ORPHAN_ENTITY,
                                     ["component:C-05"], [])
        c = deterministic_finding_id("1.0.0", FindingType.ORPHAN_ENTITY,
                                     ["component:C-06"], [])
        assert a != b and a != c

    def test_valid_finding_constructs(self):
        f = Finding(finding_id="M6-X-1234567890", version_label="1.0.0",
                    finding_type=FindingType.ORPHAN_ENTITY,
                    severity=FindingSeverity.MEDIUM, title="t", description="d",
                    confidence=0.9, entity_keys=["component:C-05"],
                    evidence=[{"kind": "entity", "key": "component:C-05"}],
                    detector="x")
        assert f.status == FindingStatus.OPEN

    def test_invalid_confidence_rejected(self):
        with pytest.raises(Exception):
            Finding(finding_id="M6-X-1234567890", version_label="1.0.0",
                    finding_type=FindingType.ORPHAN_ENTITY,
                    severity=FindingSeverity.MEDIUM, title="t",
                    description="d", confidence=5.0,
                    evidence=[{"kind": "entity", "key": "component:C-05"}],
                    detector="x")

    def test_invalid_type_rejected(self):
        with pytest.raises(Exception):
            Finding(finding_id="M6-X-1234567890", version_label="1.0.0",
                    finding_type="not_a_type", severity=FindingSeverity.MEDIUM,
                    title="t", description="d", confidence=0.5,
                    evidence=[{"kind": "entity", "key": "component:C-05"}],
                    detector="x")

    def test_invalid_evidence_kind_rejected(self):
        with pytest.raises(Exception):
            Finding(finding_id="M6-X-1234567890", version_label="1.0.0",
                    finding_type=FindingType.ORPHAN_ENTITY,
                    severity=FindingSeverity.MEDIUM, title="t",
                    description="d", confidence=0.5,
                    evidence=[{"kind": "vibes", "key": "component:C-05"}],
                    detector="x")


# --------------------------------------------------------- validator ----

class TestValidator:
    def _finding(self, **kw):
        base = dict(finding_id=None, version_label="1.0.0",
                    finding_type=FindingType.ORPHAN_ENTITY,
                    severity=FindingSeverity.MEDIUM, title="t",
                    description="d", confidence=0.9,
                    entity_keys=["component:C-05"], fact_keys=[],
                    evidence=[{"kind": "entity", "key": "component:C-05"}],
                    detector="x")
        base.update(kw)
        return Finding(**base)

    def test_ok(self):
        rep = validate_findings([self._finding()], set(), {"component:C-05"})
        assert rep.ok and rep.checked == 1

    def test_unknown_fact_key(self):
        f = self._finding(fact_keys=["nope"])
        f.evidence = list(f.evidence) + [{"kind": "fact", "key": "nope"}]
        rep = validate_findings([f], set(), {"component:C-05"})
        assert not rep.ok
        assert any(i.code == "unknown_fact_key" for i in rep.issues)

    def test_unknown_entity_key(self):
        rep = validate_findings([self._finding()], set(), set())
        assert not rep.ok
        assert any(i.code == "unknown_entity_key" for i in rep.issues)

    def test_unknown_entity_allowed_for_undefined_reference(self):
        f = self._finding(finding_type=FindingType.UNDEFINED_REFERENCE,
                          entity_keys=["interface:IF-99"])
        rep = validate_findings([f], set(), set())
        assert rep.ok

    def test_version_mismatch_empty(self):
        f = self._finding(version_label="  ")
        rep = validate_findings([f], set(), {"component:C-05"})
        assert any(i.code == "version_mismatch" for i in rep.issues)

    def test_missing_evidence(self):
        f = self._finding()
        f.evidence = []
        rep = validate_findings([f], set(), {"component:C-05"})
        assert any(i.code == "missing_evidence" for i in rep.issues)

    def test_invalid_severity_status(self):
        f = self._finding()
        f.severity = "catastrophic"          # bypass the enum (raw attr)
        f.status = "maybe"
        rep = validate_findings([f], set(), {"component:C-05"})
        codes = {i.code for i in rep.issues}
        assert "invalid_severity" in codes and "invalid_status" in codes

    def test_invalid_confidence_flagged(self):
        f = self._finding()
        f.confidence = -1.0
        rep = validate_findings([f], set(), {"component:C-05"})
        assert any(i.code == "invalid_confidence" for i in rep.issues)

    def test_duplicate_ids(self):
        rep = validate_findings(
            [self._finding(), self._finding()], set(), {"component:C-05"})
        assert any(i.code == "duplicate_finding_id" for i in rep.issues)

    def test_tampered_id_detected(self):
        f = self._finding(finding_id="M6-FORGED-0000000001")
        rep = validate_findings([f], set(), {"component:C-05"})
        assert any(i.code == "invalid_finding_id" for i in rep.issues)

    def test_malformed_entity_key(self):
        f = self._finding(entity_keys=["C-05"])
        rep = validate_findings([f], set(), {"component:C-05"})
        assert any(i.code == "malformed_entity_key" for i in rep.issues)


# --------------------------------------------------------- detectors ----

class TestDetectors:
    def test_undefined_reference_fires(self):
        ctx = make_ctx(
            [fact("f1", "component:C-01", "requires", "interface:IF-99")],
            {"component:C-01": entity("component:C-01")})
        out = detect_undefined_references(ctx)
        assert len(out) == 1
        f = out[0]
        assert f.finding_type == FindingType.UNDEFINED_REFERENCE
        assert f.severity == FindingSeverity.HIGH
        assert "interface:IF-99" in f.entity_keys
        assert f.fact_keys == ["f1"]
        assert f.provenance and f.provenance[0]["page_start"] == 3

    def test_undefined_reference_silent_when_all_defined(self):
        ctx = make_ctx(
            [fact("f1", "component:C-01", "requires", "interface:IF-01")],
            {"component:C-01": entity("component:C-01"),
             "interface:IF-01": entity("interface:IF-01")})
        assert detect_undefined_references(ctx) == []

    def test_dangling_requires_fires(self):
        ctx = make_ctx(
            [fact("f1", "component:C-01", "requires", "interface:IF-07")],
            {"component:C-01": entity("component:C-01"),
             "interface:IF-07": entity("interface:IF-07")})
        out = detect_dangling_requires(ctx)
        assert len(out) == 1
        f = out[0]
        assert f.finding_type == FindingType.DANGLING_REQUIRES
        assert set(f.entity_keys) == {"component:C-01", "interface:IF-07"}
        assert f.metadata["interface_defined"] is True

    def test_dangling_requires_not_fired_when_provided(self):
        ctx = make_ctx(
            [fact("f1", "component:C-01", "requires", "interface:IF-01"),
             fact("f2", "component:C-02", "provides", "interface:IF-01")],
            {"component:C-01": entity("component:C-01"),
             "component:C-02": entity("component:C-02"),
             "interface:IF-01": entity("interface:IF-01")})
        assert detect_dangling_requires(ctx) == []

    def test_provided_but_unconsumed_is_not_dangling(self):
        # interface with a provider but NO consumer: legitimate, no finding
        ctx = make_ctx(
            [fact("f2", "component:C-02", "provides", "interface:IF-01")],
            {"component:C-02": entity("component:C-02"),
             "interface:IF-01": entity("interface:IF-01")})
        assert detect_dangling_requires(ctx) == []

    def test_duplicate_interface_fires(self):
        ctx = make_ctx(
            [], {"interface:IF-01": entity("interface:IF-01", name="SpeedIF"),
                 "interface:IF-02": entity("interface:IF-02", name="speedif")})
        out = detect_duplicate_interfaces(ctx)
        assert len(out) == 1
        f = out[0]
        assert f.finding_type == FindingType.DUPLICATE_INTERFACE
        assert set(f.entity_keys) == {"interface:IF-01", "interface:IF-02"}
        assert f.metadata["normalized_name"] == "speedif"

    def test_duplicate_interface_ignores_repeated_references(self):
        # many requires facts targeting one interface are NOT duplicates
        ctx = make_ctx(
            [fact("f1", "component:C-01", "requires", "interface:IF-01"),
             fact("f2", "component:C-02", "requires", "interface:IF-01")],
            {"interface:IF-01": entity("interface:IF-01", name="SpeedIF"),
             "component:C-01": entity("component:C-01"),
             "component:C-02": entity("component:C-02")})
        assert detect_duplicate_interfaces(ctx) == []

    def test_conflicting_providers_fires(self):
        ctx = make_ctx(
            [fact("f1", "component:C-08", "provides", "interface:IF-13"),
             fact("f2", "component:C-10", "provides", "interface:IF-13")],
            {"component:C-08": entity("component:C-08"),
             "component:C-10": entity("component:C-10"),
             "interface:IF-13": entity("interface:IF-13")})
        out = detect_conflicting_providers(ctx)
        assert len(out) == 1
        f = out[0]
        assert f.finding_type == FindingType.CONFLICTING_PROVIDERS
        assert f.severity == FindingSeverity.HIGH
        assert set(f.entity_keys) == {"interface:IF-13", "component:C-08",
                                      "component:C-10"}
        assert f.fact_keys == ["f1", "f2"]

    def test_conflicting_providers_silent_single_provider(self):
        ctx = make_ctx(
            [fact("f1", "component:C-08", "provides", "interface:IF-13")],
            {"component:C-08": entity("component:C-08"),
             "interface:IF-13": entity("interface:IF-13")})
        assert detect_conflicting_providers(ctx) == []

    def test_orphan_entity_fires(self):
        g = nx.MultiDiGraph()
        g.add_node("component:C-01", entity_type="component")
        g.add_node("component:C-05", entity_type="component")
        g.add_edge("component:C-01", "interface:IF-01", key="f1")
        ctx = make_ctx(
            [], {"component:C-05": entity("component:C-05"),
                 "component:C-01": entity("component:C-01"),
                 "interface:IF-01": entity("interface:IF-01")}, graph=g)
        out = detect_orphan_entities(ctx)
        assert len(out) == 1
        f = out[0]
        assert f.finding_type == FindingType.ORPHAN_ENTITY
        assert f.entity_keys == ["component:C-05"]
        assert f.evidence[0].note == "degree-0 node in version graph"

    def test_orphan_entity_ignores_dependency_nodes(self):
        # degree-0 dependency nodes are intentional representation, not bugs
        g = nx.MultiDiGraph()
        g.add_node("dependency:DEP-01", entity_type="dependency")
        ctx = make_ctx(
            [], {"dependency:DEP-01": entity("dependency:DEP-01")}, graph=g)
        assert detect_orphan_entities(ctx) == []

    def test_orphan_entity_ignores_connected_nodes(self):
        g = nx.MultiDiGraph()
        g.add_edge("component:C-01", "interface:IF-01", key="f1")
        ctx = make_ctx(
            [], {"component:C-01": entity("component:C-01"),
                 "interface:IF-01": entity("interface:IF-01")}, graph=g)
        assert detect_orphan_entities(ctx) == []

    def test_unconsumed_signal_fires(self):
        ctx = make_ctx(
            [fact("f1", "interface:IF-11", "carries", "signal:SG-015")],
            {"interface:IF-11": entity("interface:IF-11"),
             "signal:SG-015": entity("signal:SG-015")})
        out = detect_unconsumed_signals(ctx)
        assert len(out) == 1
        f = out[0]
        assert f.finding_type == FindingType.UNCONSUMED_SIGNAL
        assert set(f.entity_keys) == {"interface:IF-11", "signal:SG-015"}
        assert f.fact_keys == ["f1"]

    def test_unconsumed_signal_silent_when_interface_consumed(self):
        ctx = make_ctx(
            [fact("f1", "interface:IF-11", "carries", "signal:SG-015"),
             fact("f2", "component:C-08", "requires", "interface:IF-11")],
            {"interface:IF-11": entity("interface:IF-11"),
             "signal:SG-015": entity("signal:SG-015"),
             "component:C-08": entity("component:C-08")})
        assert detect_unconsumed_signals(ctx) == []

    def test_finding_confidence_inherits_max_fact_confidence(self):
        ctx = make_ctx(
            [fact("f1", "component:C-08", "provides", "interface:IF-13",
                  conf=0.7),
             fact("f2", "component:C-10", "provides", "interface:IF-13",
                  conf=0.95)],
            {"component:C-08": entity("component:C-08"),
             "component:C-10": entity("component:C-10"),
             "interface:IF-13": entity("interface:IF-13")})
        f = detect_conflicting_providers(ctx)[0]
        assert f.confidence == 0.95

    def test_detector_output_validates(self):
        # every synthetic detector run must produce mechanically valid findings
        ctx = make_ctx(
            [fact("f1", "component:C-08", "provides", "interface:IF-13"),
             fact("f2", "component:C-10", "provides", "interface:IF-13"),
             fact("f3", "component:C-01", "requires", "interface:IF-77"),
             fact("f4", "interface:IF-11", "carries", "signal:SG-015")],
            {"component:C-08": entity("component:C-08"),
             "component:C-10": entity("component:C-10"),
             "component:C-01": entity("component:C-01"),
             "interface:IF-13": entity("interface:IF-13"),
             "interface:IF-11": entity("interface:IF-11"),
             "signal:SG-015": entity("signal:SG-015")})
        findings = (detect_undefined_references(ctx)
                    + detect_dangling_requires(ctx)
                    + detect_duplicate_interfaces(ctx)
                    + detect_conflicting_providers(ctx)
                    + detect_orphan_entities(ctx)
                    + detect_unconsumed_signals(ctx))
        rep = validate_findings(findings, known_fact_keys=set(ctx.facts),
                                known_entity_keys=set(ctx.entities))
        assert rep.ok, rep.summary()
