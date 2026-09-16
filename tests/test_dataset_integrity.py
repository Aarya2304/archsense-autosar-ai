"""M0 tests: synthetic dataset integrity (source of truth vs ground truth)."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from backend.dataset import model as M
from backend.dataset.ground_truth import compute_expected_diff


@pytest.fixture(scope="module")
def v1():
    return M.build_v1()


@pytest.fixture(scope="module")
def v2():
    return M.build_v2()


# ------------------------------------------------------------- registry ----


def test_v1_registry_shape(v1):
    assert len(v1["components"]) == 20
    assert len(v1["interfaces"]) == 25
    assert len(v1["signals"]) == 34
    assert len(v1["dependencies"]) == 24
    assert len(v1["flows"]) == 5


def test_component_ids_unique(v1):
    ids = [c.id for c in v1["components"]]
    assert len(ids) == len(set(ids))


def test_interface_provider_and_consumers_exist(v1):
    comp_ids = {c.id for c in v1["components"]}
    for i in v1["interfaces"]:
        assert i.provider in comp_ids, i.id
        for c in i.consumers:
            assert c in comp_ids, (i.id, c)


def test_signal_interfaces_exist(v1):
    iface_ids = {i.id for i in v1["interfaces"]}
    for s in v1["signals"]:
        assert s.interface_id in iface_ids, s.id


def test_dependency_endpoints_exist(v1):
    comp_ids = {c.id for c in v1["components"]}
    for d in v1["dependencies"]:
        assert d.source_id in comp_ids, d.id
        assert d.target_id in comp_ids, d.id


def test_flow_steps_exist(v1):
    comp_ids = {c.id for c in v1["components"]}
    for f in v1["flows"]:
        for step in f.steps:
            assert step in comp_ids, (f.id, step)


# ------------------------------------------------------------ v2 defects ----


def test_v2_has_defects(v2):
    assert len(v2["defects"]) == 8
    assert "D1_removed_dependency" in v2["defects"]


def test_d1_removed_dependency(v2):
    dep_ids = {d.id for d in v2["dependencies"]}
    assert "DEP-19" not in dep_ids


def test_d2_rename(v2):
    c09 = next(c for c in v2["components"] if c.id == "C-09")
    assert c09.name == "VehicleModeMgrSWC"


def test_d3_conflicting_provider_setup(v2):
    """D3: IF-13 provider flipped to C-08; prose retains C-10 (renderer)."""
    if13 = next(i for i in v2["interfaces"] if i.id == "IF-13")
    assert if13.provider == "C-08"


def test_d4_orphan_component(v2):
    c05 = next(c for c in v2["components"] if c.id == "C-05")
    assert c05 is not None  # still declared...
    refs = 0
    for i in v2["interfaces"]:
        refs += int(i.provider == "C-05") + int("C-05" in i.consumers)
    for d in v2["dependencies"]:
        refs += int(d.source_id == "C-05") + int(d.target_id == "C-05")
    assert refs == 0  # ...but referenced nowhere


def test_nv_block_keeps_window_consumer(v2):
    """Minimal orphan transform: IF-20 keeps C-02, drops only C-05."""
    if20 = next(i for i in v2["interfaces"] if i.id == "IF-20")
    assert "C-02" in if20.consumers and "C-05" not in if20.consumers


def test_d5_dropped_signal(v2):
    sig_ids = {s.id for s in v2["signals"]}
    assert "SG-015" not in sig_ids


def test_d8_new_consumer(v2):
    if03 = next(i for i in v2["interfaces"] if i.id == "IF-03")
    assert "C-04" in if03.consumers


def test_seat_flow_removed_in_v2(v2):
    flow_ids = {f.id for f in v2["flows"]}
    assert "FL-4" not in flow_ids


# ------------------------------------------------------------ expected diff ----


def test_expected_diff_matches_registry(v1, v2):
    d = compute_expected_diff(v1, v2)
    assert d["components"]["modified"] == ["C-09"]
    # D5 (SG-015) + seat signals from interfaces left empty/providerless
    assert set(d["signals"]["removed"]) == {"SG-011", "SG-012",
                                             "SG-013", "SG-015"}
    assert "DEP-19" in d["dependencies"]["removed"]
    # IF-08 loses its provider (C-05), IF-09 loses all consumers
    assert set(d["interfaces"]["removed"]) == {"IF-08", "IF-09"}
    # IF-20/IF-24 keep other consumers but lose C-05 -> modified
    assert {"IF-03", "IF-13", "IF-20", "IF-24"} \
        <= set(d["interfaces"]["modified"])
    assert d["flows"]["removed"] == ["FL-4"]


def test_expected_diff_counts(dataset):
    d = dataset["expected_diff"]
    assert len(d["components"]["modified"]) == 1
    assert len(d["interfaces"]["removed"]) == 2
    assert len(d["signals"]["removed"]) == 4


# ------------------------------------------------------------ QA pairs ----


def test_qa_pairs_count(dataset):
    assert len(dataset["qa_pairs"]) == 30


def test_qa_gold_pages_resolve(dataset):
    for qa in dataset["qa_pairs"]:
        for p in qa["gold_pages"]:
            assert p >= 1, qa["id"]


def test_unanswerable_set(dataset):
    assert len(dataset["unanswerable_questions"]) == 4


# -------------------------------------------------------- gold findings ----


def test_expected_findings_pages(dataset):
    for f in dataset["expected_findings"]:
        assert f["gold_page"] >= 1, f["defect_id"]


def test_expected_findings_checks_unique(dataset):
    checks = [f["check"] for f in dataset["expected_findings"]]
    assert len(checks) == len(set(checks))
