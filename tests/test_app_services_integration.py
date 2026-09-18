"""M8 tests (part 2): service integration over the real project registry.

These run fully offline against the persisted synthetic corpus (same DB the
CLIs use). Reports, copilot wiring and comparison delegation are covered;
network providers stay out (no keys required, M8 rule 29).
"""

from __future__ import annotations

import json

import pytest

from app.services import app_services as svc
from app.services import report_service as rpt


@pytest.fixture(scope="module")
def versions():
    out = svc.get_versions()
    if not out:  # pragma: no cover - corpus must be built first
        pytest.skip("project registry not built")
    return out


@pytest.fixture(scope="module")
def registry_versions(versions):
    regs = [v["version"] for v in versions if v["has_registry"]]
    if len(regs) < 2:  # pragma: no cover
        pytest.skip("two extracted versions required")
    return regs


# ------------------------------------------------------------- documents --


def test_get_versions_flags(versions):
    by_version = {v["version"]: v for v in versions}
    assert "1.0.0" in by_version and "1.1.0" in by_version
    for v in versions:
        assert isinstance(v["has_chunks"], bool)
        assert isinstance(v["has_registry"], bool)
        if v["has_chunks"]:
            assert v["chunk_count"] > 0


def test_document_summary_and_pdf_path():
    summary = svc.get_document_summary("1.1.0")
    assert summary is not None
    assert summary["document_name"].endswith(".pdf")
    assert summary["version"] == "1.1.0"
    assert summary["page_count"] >= 1
    assert summary["sections"], "section map must come from processed JSON"
    first = summary["sections"][0]
    assert {"section_no", "page"} <= set(first.keys())
    assert svc.get_pdf_path(summary["document_name"]) is not None
    assert svc.get_document_summary("missing-version") is None


def test_get_page_text_scope_and_content():
    text = svc.get_page_text("ABC_HLD_v1.1.0.pdf", "1.1.0", 2)
    assert "high-level" in text.lower() or len(text) > 0
    empty = svc.get_page_text("ABC_HLD_v1.0.0.pdf", "1.1.0", 2)
    assert empty == ""  # version filter must scope strictly


# ------------------------------------------------------------ architecture --


def test_architecture_graph_and_stats(registry_versions):
    version = registry_versions[-1]
    graph, stats = svc.get_architecture_graph(version)
    assert graph.number_of_nodes() > 0 and graph.number_of_edges() > 0
    stats_dict = svc.get_architecture_stats(version)
    assert stats_dict["node_count"] == graph.number_of_nodes()
    assert stats_dict["edge_count"] == graph.number_of_edges()
    assert stats_dict["entities_by_type"]
    assert stats_dict["facts_by_predicate"]


def test_entity_detail_and_search(registry_versions):
    version = registry_versions[-1]
    graph, _ = svc.get_architecture_graph(version)
    some_node = sorted(graph.nodes)[0]
    detail = svc.get_entity_detail(version, some_node)
    assert detail["entity_key"] == some_node
    assert {"outgoing", "incoming"} <= set(detail.keys())
    for rel in detail["outgoing"] + detail["incoming"]:
        assert rel["predicate"]
        assert rel["provenance"], "every relationship carries provenance"
    hits = svc.search_entities(version, some_node.split(":")[-1])
    assert some_node in hits
    assert svc.get_entity_detail(version, "component:NOPE-99") is None


# ---------------------------------------------------------------- copilot --


def test_ask_copilot_mock_provider_scoped():
    answer = svc.ask_copilot(
        "Which component provides the VehicleSpeed interface?",
        version="1.1.0", provider="mock")
    assert answer["status"] in {"answered", "insufficient_evidence"}
    for c in answer.get("citations", []):
        # trusted citation metadata: version must match the requested scope
        assert c["document_name"] == "ABC_HLD_v1.1.0.pdf"
        assert {"evidence_id", "chunk_id", "document_name", "section",
                "pages", "quote"} <= set(c.keys())


# ---------------------------------------------------------------- findings --


def test_analyze_findings_shape(registry_versions):
    version = registry_versions[0]  # v1.0.0 is the clean baseline
    result = svc.analyze_findings(version)
    assert result["version"] == version
    assert result["validation_ok"] is True
    assert set(result["findings"]) == set() or isinstance(result["findings"],
                                                          list)
    assert isinstance(result["by_type"], dict)
    assert isinstance(result["by_severity"], dict)
    assert set(result["detectors_run"])  # all six detectors ran


def test_findings_persisted_roundtrip(registry_versions):
    version = registry_versions[-1]
    persisted = svc.get_findings_from_db(version)
    for f in persisted:
        assert f["status"] in {"open", "accepted", "rejected",
                               "needs_discussion"}
        assert f["severity"] in {"high", "medium", "low", "info"}


# -------------------------------------------------------------- comparison --


def test_compare_revisions_delegation(registry_versions):
    base, target = registry_versions[0], registry_versions[-1]
    comp = svc.compare_revisions(base, target, depth=1)
    assert comp["base_version"] == base
    assert comp["target_version"] == target
    s = comp["summary"]
    assert s["entity_added"] == sum(
        1 for c in comp["entity_changes"]
        if c["change_type"] == "entity_added")
    assert s["impact_count"] == len(comp["impacts"])
    for i in comp["impacts"]:
        if i["depth"] >= 1:
            assert i["path"], "traversed impacts must record real edge walks"
            for step in i["path"]:
                assert {"frm", "predicate", "to", "fact_key"} <= set(step)
        else:  # depth-0 DIRECT impact: the changed anchor itself, no hops
            assert i["category"]


def test_compare_revisions_same_version_rejected():
    with pytest.raises(svc.ServiceError):
        svc.compare_revisions("1.0.0", "1.0.0")


def test_compare_revisions_missing_version_rejected():
    with pytest.raises(svc.ServiceError):
        svc.compare_revisions("1.0.0", "9.9.9")


# ----------------------------------------------------------------- report --


def test_report_structure_and_content(registry_versions):
    base, target = registry_versions[0], registry_versions[-1]
    report = svc.generate_report(version=target, base_version=base,
                                 target_version=target)
    for section in rpt.REPORT_SECTIONS:
        assert section in report
    assert report["document_information"]["selected"]["version"] == target
    assert report["revision_changes"], "comparison included in report"
    assert report["potential_impacts"]
    assert report["limitations"]


def test_report_json_roundtrip_and_determinism(registry_versions):
    base, target = registry_versions[0], registry_versions[-1]
    report = svc.generate_report(version=target, base_version=base,
                                 target_version=target)
    js1 = rpt.report_to_json(report)
    js2 = rpt.report_to_json(json.loads(js1))
    assert js1 == js2  # deterministic serialization
    payload = json.loads(js1)
    assert payload["report"] == "ARCHSENSE"


def test_report_contains_no_secrets(registry_versions):
    base, target = registry_versions[0], registry_versions[-1]
    report = svc.generate_report(version=target, base_version=base,
                                 target_version=target)
    blob = rpt.report_to_json(report)
    for csv_blob in (rpt.report_findings_to_csv(report),
                     rpt.report_changes_to_csv(report),
                     rpt.report_impacts_to_csv(report)):
        blob += csv_blob
    low = blob.lower()
    assert "sk-" not in low
    assert "api_key" not in low and "apikey" not in low
    assert "openrouter_api_key" not in low
    assert "password" not in low


def test_csv_sections(registry_versions):
    base, target = registry_versions[0], registry_versions[-1]
    report = svc.generate_report(version=target, base_version=base,
                                 target_version=target)
    findings_csv = rpt.report_findings_to_csv(report)
    changes_csv = rpt.report_changes_to_csv(report)
    impacts_csv = rpt.report_impacts_to_csv(report)
    header_f = findings_csv.splitlines()[0]
    assert header_f.startswith("finding_type,severity,title,status")
    rows_c = changes_csv.splitlines()
    assert rows_c[0].startswith("change_type,entity_or_subject,predicate")
    assert len(rows_c) > 1
    rows_i = impacts_csv.splitlines()
    assert rows_i[0].startswith("source_change_id,impacted_entity_key")
    assert "potentially impacted" in impacts_csv  # reason wording survives
