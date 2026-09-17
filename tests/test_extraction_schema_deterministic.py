"""M4 tests: extraction schema + deterministic extractor (offline, fast).

Covers: taxonomy enums/normalization, entity/fact model validation
(including the frozen-provenance rule), table parsing, every table
handler, prose title/provider/consumer/flow patterns, negation guarding,
owner attribution via the provider map, and full-corpus extraction
matching the M0 ground truth exactly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.extraction.deterministic import (_parse_tables,
                                              extract_deterministic)
from backend.extraction.models import (EntityType, EvidenceRef, ExtractedEntity,
                                       ExtractedFact, Predicate, Source,
                                       normalize_key)
from backend.rag.chunker import chunk_processed_json


# ------------------------------------------------------------------ schema --

def test_entity_type_enum_values():
    assert {t.value for t in EntityType} == {
        "component", "interface", "port", "signal", "dependency",
        "functional_flow"}


def test_predicate_enum_values():
    assert {p.value for p in Predicate} == {
        "provides", "requires", "depends_on", "carries", "implements",
        "participates_in"}


def test_predicate_domain_range_tables():
    assert ExtractedFact.PREDICATE_DOMAIN[Predicate.PROVIDES] is \
        EntityType.COMPONENT
    assert EntityType.INTERFACE in ExtractedFact.PREDICATE_RANGE[Predicate.REQUIRES]
    assert EntityType.SIGNAL in ExtractedFact.PREDICATE_RANGE[Predicate.CARRIES]


@pytest.mark.parametrize("raw,expected", [
    ("c-02", "component:C-02"),
    ("C-02", "component:C-02"),
    ("if-03", "interface:IF-03"),
    ("sg-018", "signal:SG-018"),
    ("dep-05", "dependency:DEP-05"),
    ("fl-1", "functional_flow:FL-1"),
    ("DoorStatusIF", "interface:doorstatusif"),
    ("VehicleSpeed", "signal:vehiclespeed"),
])
def test_normalize_key(raw, expected):
    etype = EntityType(expected.split(":")[0])
    assert normalize_key(etype, raw) == expected


def test_entity_valid_construction():
    src = Source(document_name="d.pdf", version="1.0.0", page_start=1,
                 page_end=1, chunk_id="c1")
    e = ExtractedEntity(entity_type=EntityType.COMPONENT, name="c-02",
                        attributes={"id": "C-02"},
                        evidence=EvidenceRef(source=src), confidence=0.95)
    assert e.key == "component:C-02"
    assert e.normalized_name == "C-02"   # ID normalization uppercases


def test_entity_invalid_confidence_rejected():
    src = Source(document_name="d.pdf", version="1.0.0", page_start=1,
                 page_end=1, chunk_id="c1")
    with pytest.raises(ValidationError):
        ExtractedEntity(entity_type=EntityType.COMPONENT, name="X",
                        evidence=EvidenceRef(source=src), confidence=5.0)
    with pytest.raises(ValidationError):
        ExtractedEntity(entity_type=EntityType.COMPONENT, name="X",
                        evidence=EvidenceRef(source=src), confidence=-0.1)


def test_entity_empty_name_rejected():
    src = Source(document_name="d.pdf", version="1.0.0", page_start=1,
                 page_end=1, chunk_id="c1")
    with pytest.raises(ValidationError):
        ExtractedEntity(entity_type=EntityType.COMPONENT, name="  ",
                        evidence=EvidenceRef(source=src), confidence=0.9)


def test_fact_dedupe_key_ignores_confidence():
    src = Source(document_name="d.pdf", version="1.0.0", page_start=1,
                 page_end=1, chunk_id="c1")
    ev = EvidenceRef(source=src)
    f1 = ExtractedFact(subject="component:C-02", predicate=Predicate.PROVIDES,
                       object="interface:IF-03", evidence=ev, confidence=0.9)
    f2 = ExtractedFact(subject="component:C-02", predicate=Predicate.PROVIDES,
                       object="interface:IF-03", evidence=ev, confidence=0.7)
    assert f1.dedupe_key == f2.dedupe_key
    assert f1 != f2


# ------------------------------------------------------------- extraction ----

def _chunks(version: str):
    pj = (f"data/processed/ABC_HLD_v{version}__processed.json")
    return chunk_processed_json(pj)


@pytest.fixture(scope="module")
def v1_extraction():
    return extract_deterministic(_chunks("1.0.0"))


@pytest.fixture(scope="module")
def v2_extraction():
    return extract_deterministic(_chunks("1.1.0"))


def test_deterministic_deterministic(v1_extraction):
    """Same input -> identical output (entity/fact key sets)."""
    again = extract_deterministic(_chunks("1.0.0"))
    assert {e.key for e in v1_extraction.entities} == \
        {e.key for e in again.entities}
    assert {f.dedupe_key for f in v1_extraction.facts} == \
        {f.dedupe_key for f in again.facts}


def test_table_parsing(v1_extraction):
    assert v1_extraction.stats["component_rows"] == 20
    assert v1_extraction.stats["port_rows"] == 57
    assert v1_extraction.stats["signal_dict_rows"] == 34
    assert v1_extraction.stats["dep_rows"] == 24


def test_component_entities_match_ground_truth(v1_extraction):
    gt_keys = {f"component:C-{i:02d}" for i in range(1, 21)}
    assert gt_keys <= {e.key for e in v1_extraction.entities}


def test_port_owner_attribution_exact(v1_extraction):
    """Port-table owner attribution must be exact (P-001 -> C-01 etc.)."""
    owners = {e.attributes["id"]: e.attributes["component_id"]
              for e in v1_extraction.entities
              if e.entity_type is EntityType.PORT}
    assert owners["P-001"] == "component:C-01"
    assert owners["P-005"] == "component:C-02"
    assert all(v for v in owners.values())


def test_negation_guard_no_v2_contradiction_fact(v2_extraction):
    """The v2 D6 sentence must NOT produce a C-06 requires C-08 fact."""
    for f in v2_extraction.facts:
        if f.subject == "component:C-06" and f.object == "component:C-08":
            assert f.predicate is not Predicate.REQUIRES


def test_full_extraction_matches_ground_truth_v1(dataset):
    """Canonical (service-validated) extraction == gold, exactly."""
    from backend.extraction.evaluation import gold_entities, gold_facts
    from backend.extraction.service import ExtractionService
    res = ExtractionService().extract_from_chunks(
        _chunks("1.0.0"), document_name="t", version="1.0.0")
    gt_v = dataset["entities"]["v1"]
    pred_e = {e.key for e in res.entities}
    pred_f = {(f.subject, f.predicate.value, f.object)
              for f in res.facts}
    assert pred_e == gold_entities(gt_v, dataset["ports"]["v1"])
    assert pred_f == gold_facts(gt_v, dataset["ports"]["v1"])


def test_full_extraction_matches_ground_truth_v2(dataset):
    from backend.extraction.evaluation import gold_entities, gold_facts
    from backend.extraction.service import ExtractionService
    res = ExtractionService().extract_from_chunks(
        _chunks("1.1.0"), document_name="t", version="1.1.0")
    gt_v = dataset["entities"]["v2"]
    pred_e = {e.key for e in res.entities}
    pred_f = {(f.subject, f.predicate.value, f.object)
              for f in res.facts}
    assert pred_e == gold_entities(gt_v, dataset["ports"]["v2"])
    assert pred_f == gold_facts(gt_v, dataset["ports"]["v2"])


def test_planted_defect_reflected_in_v2_extraction(v2_extraction):
    """D3: v2 documents C-08 as the VehicleSpeedIF provider (conflict)."""
    prov = {(f.subject, f.object) for f in v2_extraction.facts
            if f.predicate is Predicate.PROVIDES
            and f.object == "interface:IF-13"}
    assert ("component:C-08", "interface:IF-13") in prov
    assert ("component:C-10", "interface:IF-13") not in prov


def test_port_table_owner_via_provider_map(v1_extraction):
    """Name-form vs ID-form surface variants (e.g. ClimateRequestIF from
    the 4.10 title vs IF-10 from a port table) are BOTH extracted raw and
    must collapse to the same fact after service canonicalization — the
    C-06 provides ClimateRequestIF(=IF-10) fact must exist once."""
    from backend.extraction.service import ExtractionService
    res = ExtractionService().extract_from_chunks(
        _chunks("1.0.0"), document_name="t", version="1.0.0")
    fk = [(f.subject, f.object) for f in res.facts
          if f.subject == "component:C-06"
          and f.predicate is Predicate.PROVIDES]
    assert fk == [("component:C-06", "interface:IF-10")]
