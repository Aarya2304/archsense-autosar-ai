"""
Ground-truth builder (M0).

Renders HLD v1 and v2 PDFs, then assembles data/ground_truth/ground_truth.json
containing everything the evaluation harness (M8) needs:

- canonical entity registries for both versions
- page/section index resolved from the actual rendered PDFs
- the expected v1->v2 diff (computed mechanically from the two registries)
- expected findings for each planted defect (with gold page references)
- ~30 Q&A pairs with gold sections/pages for citation evaluation

Because ground truth is derived from the same source-of-truth model that
produced the PDFs, entity/page drift between document and ground truth is
impossible by construction.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from backend.config import GENERATED_DIR, GROUND_TRUTH_DIR, SAMPLE_DOCS_DIR
from backend.dataset import model as M
from backend.dataset import render_pdf

GROUND_TRUTH_PATH = GROUND_TRUTH_DIR / "ground_truth.json"
V1_PDF_PATH = SAMPLE_DOCS_DIR / "ABC_HLD_v1.0.0.pdf"
V2_PDF_PATH = SAMPLE_DOCS_DIR / "ABC_HLD_v1.1.0.pdf"


# --------------------------------------------------------------------------
# Diff computation (mechanical, registry-based)
# --------------------------------------------------------------------------


def compute_expected_diff(v1: dict, v2: dict) -> dict:
    """Compute the expected v1 -> v2 diff from the two registries."""
    return {
        "components": _diff_entities(v1["components"], v2["components"],
                                     key=lambda c: c.id),
        "interfaces": _diff_entities(v1["interfaces"], v2["interfaces"],
                                     key=lambda i: i.id),
        "signals": _diff_entities(v1["signals"], v2["signals"],
                                  key=lambda s: s.id),
        "dependencies": _diff_entities(v1["dependencies"], v2["dependencies"],
                                       key=lambda d: d.id),
        "flows": _diff_entities(v1["flows"], v2["flows"],
                                key=lambda f: f.id),
    }


def _diff_entities(v1_list, v2_list, key) -> dict:
    v1_map = {key(e): e for e in v1_list}
    v2_map = {key(e): e for e in v2_list}
    added = sorted(set(v2_map) - set(v1_map))
    removed = sorted(set(v1_map) - set(v2_map))
    modified = sorted(
        eid for eid in set(v1_map) & set(v2_map)
        if asdict(v1_map[eid]) != asdict(v2_map[eid]))
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged": sorted(set(v1_map) & set(v2_map) - set(modified)),
    }


# --------------------------------------------------------------------------
# Expected findings (gold) for planted defects
# --------------------------------------------------------------------------

# Gold pages are resolved dynamically from the *rendered* registries:
# v2 removed entities, so section numbers shift; positions must be computed
# from the actual v2 lists (not hardcoded v1 numbering).
_DEFECT_SPEC = [
    # defect_id, check, severity, (version, entity_type, entity_id), description
    ("D1_removed_dependency", "removed_dependency", "HIGH",
     ("v1", "deps", "DEP-19"),
     "Dependency DEP-19 (DiagManager -> RteAdapter) present in v1 is "
     "absent in v2 with no replacement."),
    ("D2_stale_component_reference", "stale_reference", "MEDIUM",
     ("v2", "interfaces", "IF-12"),
     "IF-12 consumer list references old component name VehicleModeSWC "
     "after rename to VehicleModeMgrSWC."),
    ("D3_conflicting_provider", "conflicting_provider", "HIGH",
     ("v2", "interfaces", "IF-13"),
     "VehicleSpeedIF (IF-13) has conflicting provider statements: table "
     "says C-08, prose says C-10."),
    ("D4_orphan_component", "orphan_component", "MEDIUM",
     ("v2", "components", "C-05"),
     "SeatAdjustSWC (C-05) has no interfaces, dependencies, or flows in "
     "v2."),
    ("D5_dropped_signal_referenced", "dropped_signal_reference", "MEDIUM",
     ("v2", "interfaces", "IF-11"),
     "IF-11 prose still describes KeyAuthStatus (SG-015) although the "
     "signal was removed."),
    ("D6_contradictory_statement", "contradiction", "HIGH",
     ("v2", "deps", "DEP-05"),
     "v2 states ClimateInterfaceSWC does not require BodyControlSWC while "
     "DEP-05 lists that dependency."),
    ("D7_port_count_mismatch", "prose_table_mismatch", "LOW",
     ("v2", "interfaces", "IF-04"),
     "IF-04 prose port count does not match its port table."),
    ("D8_new_consumer_of_changed_interface", "impact_relevant_change",
     "INFO", ("v2", "interfaces", "IF-03"),
     "WindowStatusIF (IF-03) gained consumer C-04 (LightControlSWC) in "
     "v2."),
]


def _section_for_entity(version: str, entity_type: str, entity_id: str,
                        registry: dict) -> str:
    """Map an entity to its HLD section number from the given registry."""
    if entity_type == "components":
        pos = next(i for i, c in enumerate(registry["components"])
                   if c.id == entity_id)
        return f"3.2.{pos + 1}"
    if entity_type == "interfaces":
        pos = next(i for i, x in enumerate(registry["interfaces"])
                   if x.id == entity_id)
        return f"4.{pos + 1}"
    if entity_type == "deps":
        pos = next(i for i, d in enumerate(registry["dependencies"])
                   if d.id == entity_id)
        return f"6.2.{pos + 1}"
    raise ValueError(f"unknown entity type: {entity_type}")


def build_expected_findings(page_index_v1: dict[str, int],
                            page_index_v2: dict[str, int],
                            v1: dict, v2: dict) -> list[dict]:
    """Assemble gold findings with page numbers resolved from the PDFs."""
    findings = []
    for defect_id, check, severity, loc, description in _DEFECT_SPEC:
        version, entity_type, entity_id = loc
        registry = v1 if version == "v1" else v2
        section = _section_for_entity(version, entity_type, entity_id,
                                      registry)
        page_index = page_index_v1 if version == "v1" else page_index_v2
        page = _resolve_page(page_index, section)
        findings.append({
            "defect_id": defect_id,
            "check": check,
            "severity": severity,
            "description": description,
            "gold_version": version,
            "gold_section": section,
            "gold_page": page,
        })
    return findings


def _resolve_page(page_index: dict[str, int], section: str) -> int:
    if section in page_index:
        return page_index[section]
    prefix_matches = {k: v for k, v in page_index.items()
                      if k.startswith(section + ".") or k == section}
    if prefix_matches:
        return min(prefix_matches.values())
    return 0  # unresolved; evaluation treats 0 as "page unknown"


# --------------------------------------------------------------------------
# Q&A pairs (gold citations)
# --------------------------------------------------------------------------


def build_qa_pairs(page_index_v1: dict[str, int]) -> list[dict]:
    """Deterministic Q&A set with gold sections/pages (v1 baseline)."""
    def page(section: str) -> int:
        return _resolve_page(page_index_v1, section)

    return [
        {"id": "QA-01",
         "question": "Which component is the central arbiter in the ABC "
                     "architecture?",
         "expected_answer": "BodyControlSWC (C-08) is the central arbiter; "
                            "it resolves actuator command conflicts and "
                            "applies safety interlocks.",
         "gold_sections": ["2", "3.2.8"],
         "gold_pages": [page("2"), page("3.2.8")]},
        {"id": "QA-02",
         "question": "Which components provide the VehicleSpeed signal and "
                     "who consumes it?",
         "expected_answer": "SpeedProviderSWC (C-10) provides VehicleSpeedIF "
                            "(IF-13); consumers are DoorControlSWC (C-01) "
                            "and WindowLiftSWC (C-02).",
         "gold_sections": ["4.13", "5"],
         "gold_pages": [page("4.13"), page("5")]},
        {"id": "QA-03",
         "question": "What signals are carried by the DoorStatusIF "
                     "interface?",
         "expected_answer": "DoorStatus (uint8) and ChildLockActive "
                            "(boolean).",
         "gold_sections": ["4.1", "5"],
         "gold_pages": [page("4.1"), page("5")]},
        {"id": "QA-04",
         "question": "List the components in the CAN communication path in "
                     "order.",
         "expected_answer": "Application SWC -> RteAdapter (C-11) -> Com "
                            "(C-12) -> PduR (C-13) -> CanIf (C-14) -> "
                            "CanDriver (C-15).",
         "gold_sections": ["2"],
         "gold_pages": [page("2")]},
        {"id": "QA-05",
         "question": "Which components depend on the NvM and for what?",
         "expected_answer": "WindowLiftSWC (C-02) persists anti-pinch "
                            "thresholds; SeatAdjustSWC (C-05) persists "
                            "memory profiles (DEP-15, DEP-16).",
         "gold_sections": ["6.1", "6.2.15", "6.2.16"],
         "gold_pages": [page("6.1"), page("6.2.15"), page("6.2.16")]},
        {"id": "QA-06",
         "question": "What is the trigger and flow of the Keyless Unlock "
                     "function?",
         "expected_answer": "Trigger: authenticated key approach detected. "
                            "Flow: KeylessEntrySWC -> BodyControlSWC -> "
                            "DoorControlSWC -> IoHwAb.",
         "gold_sections": ["7.3"],
         "gold_pages": [page("7.3")]},
        {"id": "QA-07",
         "question": "Which interface does WindowLiftSWC provide and which "
                     "components consume it?",
         "expected_answer": "WindowStatusIF (IF-03), consumed by "
                            "BodyControlSWC (C-08).",
         "gold_sections": ["4.3"],
         "gold_pages": [page("4.3")]},
        {"id": "QA-08",
         "question": "What datatype and unit does the WindowPosition signal "
                     "have?",
         "expected_answer": "uint8, measured in percent (%).",
         "gold_sections": ["5"],
         "gold_pages": [page("5")]},
        {"id": "QA-09",
         "question": "Which components require IoHwAb inputs?",
         "expected_answer": "DoorControlSWC, WindowLiftSWC, "
                            "MirrorAdjustSWC, LightControlSWC, and "
                            "SeatAdjustSWC (DEP-20..DEP-24).",
         "gold_sections": ["6.1"],
         "gold_pages": [page("6.1")]},
        {"id": "QA-10",
         "question": "How many components are in the Application layer and "
                     "what are their IDs?",
         "expected_answer": "Ten components: C-01 through C-10.",
         "gold_sections": ["2", "3.1"],
         "gold_pages": [page("2"), page("3.1")]},
        {"id": "QA-11",
         "question": "What does the EcuM require from the CanSm?",
         "expected_answer": "During shutdown, EcuM requires communication "
                            "channel control from the CanSm (DEP-17).",
         "gold_sections": ["6.2.17"],
         "gold_pages": [page("6.2.17")]},
        {"id": "QA-12",
         "question": "Which flows include the CanDriver?",
         "expected_answer": "Door Lock Actuation (FL-1), One-Touch Window "
                            "Lift (FL-2), and Bus-Off Recovery (FL-5).",
         "gold_sections": ["7.1", "7.2", "7.5"],
         "gold_pages": [page("7.1"), page("7.2"), page("7.5")]},
        {"id": "QA-13",
         "question": "What is stored in NVRAM according to the "
                     "assumptions?",
         "expected_answer": "Up to three seat memory profiles per user "
                            "(assumption A2).",
         "gold_sections": ["8.1"],
         "gold_pages": [page("8.1")]},
        {"id": "QA-14",
         "question": "Which interface does the DiagManager use for "
                     "diagnostic requests?",
         "expected_answer": "DiagRequestIF (IF-22), provided by DiagManager "
                            "(C-19) and consumed by RteAdapter (C-11).",
         "gold_sections": ["4.22"],
         "gold_pages": [page("4.22")]},
        {"id": "QA-15",
         "question": "What safety interlock does the BodyControlSWC apply "
                     "using vehicle speed?",
         "expected_answer": "Anti-pinch interlocks: commands are released "
                            "only when vehicle speed interlocks permit; "
                            "anti-pinch is mandatory below 5 km/h (A3).",
         "gold_sections": ["2", "8.1"],
         "gold_pages": [page("2"), page("8.1")]},
        {"id": "QA-16",
         "question": "Which signals belong to the IoHwDoorIF interface?",
         "expected_answer": "DoorSwitchRaw (uint8) and WindowMotorCurrent "
                            "(uint16, mA).",
         "gold_sections": ["4.23", "5"],
         "gold_pages": [page("4.23"), page("5")]},
        {"id": "QA-17",
         "question": "Who consumes the VehicleModeIF interface?",
         "expected_answer": "BodyControlSWC (C-08), DoorControlSWC (C-01), "
                            "and WindowLiftSWC (C-02).",
         "gold_sections": ["4.12"],
         "gold_pages": [page("4.12")]},
        {"id": "QA-18",
         "question": "What happens when the CAN controller enters bus-off?",
         "expected_answer": "CanDriver reports the fault, CanSm executes "
                            "the recovery sequence, and EcuM is informed "
                            "(FL-5).",
         "gold_sections": ["7.5"],
         "gold_pages": [page("7.5")]},
        {"id": "QA-19",
         "question": "Which component provides the EcuModeIF and who "
                     "consumes it?",
         "expected_answer": "Provided by EcuM (C-18); consumed by CanSm "
                            "(C-16) and Com (C-12).",
         "gold_sections": ["4.21"],
         "gold_pages": [page("4.21")]},
        {"id": "QA-20",
         "question": "Describe the Seat Memory Recall flow.",
         "expected_answer": "NvM provides the persisted profile, "
                            "SeatAdjustSWC computes motor targets, "
                            "BodyControlSWC arbitrates, IoHwAb drives the "
                            "seat motors (FL-4).",
         "gold_sections": ["7.4"],
         "gold_pages": [page("7.4")]},
        {"id": "QA-21",
         "question": "What is the CAN bus bit rate assumption?",
         "expected_answer": "500 kbit/s (assumption A1).",
         "gold_sections": ["8.1"],
         "gold_pages": [page("8.1")]},
        {"id": "QA-22",
         "question": "Which components communicate over the ComPduRIF?",
         "expected_answer": "Com (C-12) provides it; PduR (C-13) consumes "
                            "it.",
         "gold_sections": ["4.16"],
         "gold_pages": [page("4.16")]},
        {"id": "QA-23",
         "question": "How does the Door Lock Actuation flow start and which "
                     "component debounces the switch input?",
         "expected_answer": "It starts when the driver operates the door "
                            "lock switch; IoHwAb debounces the raw input "
                            "(FL-1).",
         "gold_sections": ["7.1"],
         "gold_pages": [page("7.1")]},
        {"id": "QA-24",
         "question": "Which dependency IDs link application SWCs to the "
                     "BodyControlSWC for commands?",
         "expected_answer": "DEP-01 (Door), DEP-02 (Window), DEP-03 "
                            "(Mirror), DEP-04 (Seat), DEP-05 (Climate).",
         "gold_sections": ["6.1"],
         "gold_pages": [page("6.1")]},
        {"id": "QA-25",
         "question": "What does the SpeedProviderSWC do?",
         "expected_answer": "It provides filtered vehicle speed for "
                            "anti-pinch and child-lock interlocks.",
         "gold_sections": ["3.2.10"],
         "gold_pages": [page("3.2.10")]},
        {"id": "QA-26",
         "question": "Which interface carries the CanFrame signal?",
         "expected_answer": "PduCanIfIF (IF-17).",
         "gold_sections": ["4.17", "5"],
         "gold_pages": [page("4.17"), page("5")]},
        {"id": "QA-27",
         "question": "What is an R-port and which component has R-ports on "
                     "VehicleModeIF?",
         "expected_answer": "An R-port is a required port; "
                            "BodyControlSWC, DoorControlSWC, and "
                            "WindowLiftSWC hold R-ports on VehicleModeIF.",
         "gold_sections": ["1.5", "4.12"],
         "gold_pages": [page("1.5"), page("4.12")]},
        {"id": "QA-28",
         "question": "How many signals are in the signal dictionary?",
         "expected_answer": "34 signals (SG-001 through SG-034).",
         "gold_sections": ["5"],
         "gold_pages": [page("5")]},
        {"id": "QA-29",
         "question": "Which BSW module routes PDUs between COM and CanIf?",
         "expected_answer": "PduR (C-13).",
         "gold_sections": ["3.1", "3.2.13"],
         "gold_pages": [page("3.1"), page("3.2.13")]},
        {"id": "QA-30",
         "question": "What is out of scope of this HLD?",
         "expected_answer": "Detailed timing analysis, AUTOSAR OS "
                            "configuration, and ECU resource files.",
         "gold_sections": ["1.2"],
         "gold_pages": [page("1.2")]},
    ]


# --------------------------------------------------------------------------
# Unanswerable questions (refusal gold)
# --------------------------------------------------------------------------

UNANSWERABLE_QUESTIONS = [
    {"id": "QA-U1",
     "question": "What is the brake pressure of the front axle?",
     "reason": "Braking system is not part of the body domain HLD."},
    {"id": "QA-U2",
     "question": "Which AUTOSAR OS task scheduling is configured?",
     "reason": "OS configuration is explicitly out of scope (Section 1.2)."},
    {"id": "QA-U3",
     "question": "What is the price of the ABC ECU?",
     "reason": "Commercial data is not in an HLD."},
    {"id": "QA-U4",
     "question": "How is the airbag deployment logic implemented?",
     "reason": "Airbag functions are not part of this architecture."},
]


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def build_all(force: bool = False) -> dict:
    """
    Render both PDFs and write the ground truth JSON.

    Returns the ground truth dict. Idempotent: regenerates everything when
    ``force`` is true, otherwise skips if artifacts already exist.
    """
    SAMPLE_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    idx_v1_path = GENERATED_DIR / "page_index_v1.json"
    idx_v2_path = GENERATED_DIR / "page_index_v2.json"

    if (not force) and V1_PDF_PATH.exists() and V2_PDF_PATH.exists() \
            and GROUND_TRUTH_PATH.exists() and idx_v1_path.exists() \
            and idx_v2_path.exists():
        return json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))

    idx_v1 = render_pdf.render_v1(V1_PDF_PATH, idx_v1_path)
    idx_v2 = render_pdf.render_v2(V2_PDF_PATH, idx_v2_path)

    v1 = M.build_v1()
    v2 = M.build_v2()

    ground_truth = {
        "dataset": {
            "name": "ABC synthetic HLD corpus",
            "disclaimer": M.SECURITY_BANNER,
            "v1_pdf": V1_PDF_PATH.name,
            "v2_pdf": V2_PDF_PATH.name,
            "v1_version": v1["version"],
            "v2_version": v2["version"],
        },
        "page_index": {"v1": idx_v1, "v2": idx_v2},
        "entities": {
            "v1": {
                "components": [asdict(c) for c in v1["components"]],
                "interfaces": [asdict(i) for i in v1["interfaces"]],
                "signals": [asdict(s) for s in v1["signals"]],
                "dependencies": [asdict(d) for d in v1["dependencies"]],
                "flows": [asdict(f) for f in v1["flows"]],
            },
            "v2": {
                "components": [asdict(c) for c in v2["components"]],
                "interfaces": [asdict(i) for i in v2["interfaces"]],
                "signals": [asdict(s) for s in v2["signals"]],
                "dependencies": [asdict(d) for d in v2["dependencies"]],
                "flows": [asdict(f) for f in v2["flows"]],
            },
        },
        "ports": {
            "v1": [asdict(p) for p in render_pdf._v1_ports()],
            "v2": [asdict(p) for p in render_pdf._v2_ports(v2)],
        },
        "expected_diff": compute_expected_diff(v1, v2),
        "expected_findings": build_expected_findings(
            idx_v1, idx_v2, v1, v2),
        "qa_pairs": build_qa_pairs(idx_v1),
        "unanswerable_questions": UNANSWERABLE_QUESTIONS,
        "defect_catalogue": M.DEFECTS,
    }

    GROUND_TRUTH_PATH.write_text(json.dumps(ground_truth, indent=2),
                                 encoding="utf-8")
    return ground_truth


def load_ground_truth() -> dict:
    if not GROUND_TRUTH_PATH.exists():
        return build_all()
    return json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    gt = build_all(force=True)
    n_qa = len(gt["qa_pairs"])
    n_find = len(gt["expected_findings"])
    print(f"Ground truth written to {GROUND_TRUTH_PATH}")
    print(f"Q&A pairs: {n_qa} | expected findings: {n_find} | "
          f"v1 components: {len(gt['entities']['v1']['components'])}")
