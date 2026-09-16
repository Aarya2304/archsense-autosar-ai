"""
Source-of-truth model for the synthetic AUTOSAR-style HLD dataset (M0).

This module is THE single source of truth for the synthetic corpus. The PDF
renderer, the ground-truth builder, and the evaluation harness all consume the
same model, so ground truth and document content can never drift apart.

The architecture is deliberately realistic AUTOSAR-style ("Adaptive Body
Controller" ECU) while being entirely synthetic: no content represents any
real Tata/customer program. Every document statement is generated from this
model, which is what makes mechanical evaluation possible.

HLD v2 applies a fixed set of planted defects (see DEFECTS) so that the
analysis engine and revision comparator have known, expected findings.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ===========================================================================
# Core entity types
# ===========================================================================


@dataclass
class SoTComponent:
    """A software component (AUTOSAR-style SWC or BSW module)."""

    id: str  # e.g. "C-01"
    name: str  # e.g. "DoorControlSWC"
    type: str  # SWC / BSW / RTE / ECU-Abstraction
    description: str
    layer: str  # e.g. "Application", "Service", "MCAL"


@dataclass
class SoTPort:
    """A port on a component; the element of an interface it realizes/uses."""

    id: str  # e.g. "P-010"
    component_id: str
    interface_id: str
    direction: str  # "provides" | "requires"
    page_hint: int | None = None  # filled during generation (for GT)


@dataclass
class SoTInterface:
    """A client-server / sender-receiver interface between components."""

    id: str  # e.g. "IF-01"
    name: str  # e.g. "DoorStatusIF"
    kind: str  # S-R / C-S
    provider: str  # component id (must be unique per interface in v1)
    consumers: list[str]  # component ids


@dataclass
class SoTSignal:
    """A signal carried by an interface."""

    id: str  # e.g. "SG-001"
    name: str  # e.g. "DoorStatus"
    datatype: str  # e.g. uint8
    unit: str  # e.g. "-", "%", "degC"
    interface_id: str


@dataclass
class SoTDependency:
    """A directed dependency between two components."""

    id: str  # e.g. "DEP-01"
    source_id: str
    target_id: str
    relationship: str  # depends_on / uses / requires
    evidence: str  # quoted sentence from the HLD
    page_hint: int | None = None


@dataclass
class SoTFlow:
    """An end-to-end functional flow across components."""

    id: str  # e.g. "FL-1"
    name: str
    steps: list[str]  # ordered component ids
    trigger: str
    description: str


# ===========================================================================
# HLD v1 — the baseline architecture (all consistent on purpose)
# ===========================================================================

COMPONENTS_V1: list[SoTComponent] = [
    SoTComponent("C-01", "DoorControlSWC", "SWC",
                 "Controls door lock, unlock, and child-lock actuation based on switch inputs and vehicle speed constraints.",
                 "Application"),
    SoTComponent("C-02", "WindowLiftSWC", "SWC",
                 "Manages one-touch window lift, anti-pinch protection, and window position learning.",
                 "Application"),
    SoTComponent("C-03", "MirrorAdjustSWC", "SWC",
                 "Controls mirror folding, heating, and position adjustment requests.",
                 "Application"),
    SoTComponent("C-04", "LightControlSWC", "SWC",
                 "Controls interior ambient lighting and welcome light sequences.",
                 "Application"),
    SoTComponent("C-05", "SeatAdjustSWC", "SWC",
                 "Handles seat position and lumbar adjustment requests with memory profiles.",
                 "Application"),
    SoTComponent("C-06", "ClimateInterfaceSWC", "SWC",
                 "Bridges body domain functions to climate blower and recirculation actuators.",
                 "Application"),
    SoTComponent("C-07", "KeylessEntrySWC", "SWC",
                 "Processes keyless authentication challenges and unlocks authorization.",
                 "Application"),
    SoTComponent("C-08", "BodyControlSWC", "SWC",
                 "Central body domain arbiter; resolves actuator command conflicts and applies safety interlocks.",
                 "Application"),
    SoTComponent("C-09", "VehicleModeSWC", "SWC",
                 "Maintains vehicle mode state (park, drive, crash) and distributes mode changes.",
                 "Application"),
    SoTComponent("C-10", "SpeedProviderSWC", "SWC",
                 "Provides filtered vehicle speed for anti-pinch and child-lock interlocks.",
                 "Application"),
    SoTComponent("C-11", "RteAdapter", "RTE",
                 "RTE adapter layer mapping SWC ports onto BSW communication services.",
                 "RTE"),
    SoTComponent("C-12", "Com", "BSW",
                 "AUTOSAR COM module; signal packing, transmission cycles, and timeout monitoring.",
                 "Service"),
    SoTComponent("C-13", "PduR", "BSW",
                 "PDU router; routes PDUs between COM, CanIf, and transport layers.",
                 "Service"),
    SoTComponent("C-14", "CanIf", "BSW",
                 "CAN interface; multiplexes PDUs onto CAN controller channels.",
                 "Service"),
    SoTComponent("C-15", "CanDriver", "BSW",
                 "CAN driver for the body controller CAN channel with hardware filter configuration.",
                 "ECU-Abstraction"),
    SoTComponent("C-16", "CanSm", "BSW",
                 "CAN state manager; coordinates bus-off recovery and channel start/stop.",
                 "Service"),
    SoTComponent("C-17", "NvM", "BSW",
                 "NVRAM manager; persists window pinch thresholds and seat memory profiles.",
                 "Service"),
    SoTComponent("C-18", "EcuM", "BSW",
                 "ECU state manager; coordinates startup, shutdown, and sleep transitions.",
                 "Service"),
    SoTComponent("C-19", "DiagManager", "BSW",
                 "Unified diagnostic services manager for UDS-based diagnostics.",
                 "Service"),
    SoTComponent("C-20", "IoHwAb", "ECU-Abstraction",
                 "I/O hardware abstraction mapping physical pins to logical signals (door switches, window motors, mirror motors).",
                 "ECU-Abstraction"),
]

INTERFACES_V1: list[SoTInterface] = [
    SoTInterface("IF-01", "DoorStatusIF", "S-R", "C-01", ["C-08"]),
    SoTInterface("IF-02", "DoorCommandIF", "C-S", "C-08", ["C-01"]),
    SoTInterface("IF-03", "WindowStatusIF", "S-R", "C-02", ["C-08"]),
    SoTInterface("IF-04", "WindowCommandIF", "C-S", "C-08", ["C-02"]),
    SoTInterface("IF-05", "MirrorStatusIF", "S-R", "C-03", ["C-08"]),
    SoTInterface("IF-06", "MirrorCommandIF", "C-S", "C-08", ["C-03"]),
    SoTInterface("IF-07", "InteriorLightIF", "S-R", "C-04", ["C-08"]),
    SoTInterface("IF-08", "SeatStatusIF", "S-R", "C-05", ["C-08"]),
    SoTInterface("IF-09", "SeatCommandIF", "C-S", "C-08", ["C-05"]),
    SoTInterface("IF-10", "ClimateRequestIF", "C-S", "C-06", ["C-08"]),
    SoTInterface("IF-11", "KeyAuthIF", "S-R", "C-07", ["C-08"]),
    SoTInterface("IF-12", "VehicleModeIF", "S-R", "C-09", ["C-08", "C-01", "C-02"]),
    SoTInterface("IF-13", "VehicleSpeedIF", "S-R", "C-10", ["C-01", "C-02"]),
    SoTInterface("IF-14", "RteComIF", "S-R", "C-11", ["C-12"]),
    SoTInterface("IF-15", "RtePduIF", "C-S", "C-12", ["C-11"]),
    SoTInterface("IF-16", "ComPduRIF", "C-S", "C-12", ["C-13"]),
    SoTInterface("IF-17", "PduCanIfIF", "C-S", "C-13", ["C-14"]),
    SoTInterface("IF-18", "CanIfDriverIF", "C-S", "C-14", ["C-15"]),
    SoTInterface("IF-19", "CanSmControlIF", "C-S", "C-16", ["C-15"]),
    SoTInterface("IF-20", "NvBlockIF", "C-S", "C-17", ["C-02", "C-05"]),
    SoTInterface("IF-21", "EcuModeIF", "S-R", "C-18", ["C-16", "C-12"]),
    SoTInterface("IF-22", "DiagRequestIF", "C-S", "C-19", ["C-11"]),
    SoTInterface("IF-23", "IoHwDoorIF", "S-R", "C-20", ["C-01", "C-02"]),
    SoTInterface("IF-24", "IoHwMirrorSeatIF", "S-R", "C-20", ["C-03", "C-05"]),
    SoTInterface("IF-25", "IoHwLightIF", "S-R", "C-20", ["C-04"]),
]

SIGNALS_V1: list[SoTSignal] = [
    SoTSignal("SG-001", "DoorStatus", "uint8", "-", "IF-01"),
    SoTSignal("SG-002", "ChildLockActive", "boolean", "-", "IF-01"),
    SoTSignal("SG-003", "DoorLockRequest", "uint8", "-", "IF-02"),
    SoTSignal("SG-004", "WindowPosition", "uint8", "%", "IF-03"),
    SoTSignal("SG-005", "AntiPinchTriggered", "boolean", "-", "IF-03"),
    SoTSignal("SG-006", "WindowLiftCommand", "sint8", "-", "IF-04"),
    SoTSignal("SG-007", "MirrorFoldState", "boolean", "-", "IF-05"),
    SoTSignal("SG-008", "MirrorPosition", "uint8", "deg", "IF-05"),
    SoTSignal("SG-009", "MirrorAdjustCommand", "uint8", "-", "IF-06"),
    SoTSignal("SG-010", "InteriorLightLevel", "uint8", "%", "IF-07"),
    SoTSignal("SG-011", "SeatPosition", "uint16", "mm", "IF-08"),
    SoTSignal("SG-012", "SeatMemorySlot", "uint8", "-", "IF-08"),
    SoTSignal("SG-013", "SeatAdjustCommand", "sint8", "-", "IF-09"),
    SoTSignal("SG-014", "BlowerLevelRequest", "uint8", "%", "IF-10"),
    SoTSignal("SG-015", "KeyAuthStatus", "uint8", "-", "IF-11"),
    SoTSignal("SG-016", "VehicleMode", "uint8", "-", "IF-12"),
    SoTSignal("SG-017", "CrashSignal", "boolean", "-", "IF-12"),
    SoTSignal("SG-018", "VehicleSpeed", "uint16", "km/h", "IF-13"),
    SoTSignal("SG-019", "SpeedValidity", "boolean", "-", "IF-13"),
    SoTSignal("SG-020", "ComSignalBatch1", "uint8[8]", "-", "IF-14"),
    SoTSignal("SG-021", "ComSignalBatch2", "uint8[8]", "-", "IF-15"),
    SoTSignal("SG-022", "PduPayload", "uint8[64]", "-", "IF-16"),
    SoTSignal("SG-023", "CanFrame", "uint8[8]", "-", "IF-17"),
    SoTSignal("SG-024", "CanFrameRaw", "uint8[8]", "-", "IF-18"),
    SoTSignal("SG-025", "CanChannelState", "uint8", "-", "IF-19"),
    SoTSignal("SG-026", "NvBlockStatus", "uint8", "-", "IF-20"),
    SoTSignal("SG-027", "EcuModeState", "uint8", "-", "IF-21"),
    SoTSignal("SG-028", "DiagRequestPayload", "uint8[255]", "-", "IF-22"),
    SoTSignal("SG-029", "DoorSwitchRaw", "uint8", "-", "IF-23"),
    SoTSignal("SG-030", "WindowMotorCurrent", "uint16", "mA", "IF-23"),
    SoTSignal("SG-031", "MirrorMotorFeedback", "uint16", "mA", "IF-24"),
    SoTSignal("SG-032", "SeatMotorFeedback", "uint16", "mA", "IF-24"),
    SoTSignal("SG-033", "LightSwitchRaw", "uint8", "-", "IF-25"),
    SoTSignal("SG-034", "AmbientLightFeedback", "uint8", "%", "IF-25"),
]

DEPS_V1: list[SoTDependency] = [
    SoTDependency("DEP-01", "C-01", "C-08", "requires",
                  "The DoorControlSWC requires door lock and unlock commands from the BodyControlSWC."),
    SoTDependency("DEP-02", "C-02", "C-08", "requires",
                  "The WindowLiftSWC requires window lift commands from the BodyControlSWC."),
    SoTDependency("DEP-03", "C-03", "C-08", "requires",
                  "The MirrorAdjustSWC requires mirror adjustment commands from the BodyControlSWC."),
    SoTDependency("DEP-04", "C-05", "C-08", "requires",
                  "The SeatAdjustSWC requires seat adjustment commands from the BodyControlSWC."),
    SoTDependency("DEP-05", "C-06", "C-08", "requires",
                  "The ClimateInterfaceSWC requires blower control authorization from the BodyControlSWC."),
    SoTDependency("DEP-06", "C-08", "C-09", "requires",
                  "The BodyControlSWC requires vehicle mode information from the VehicleModeSWC before releasing actuator commands."),
    SoTDependency("DEP-07", "C-08", "C-10", "requires",
                  "The BodyControlSWC requires filtered vehicle speed from the SpeedProviderSWC for anti-pinch interlocks."),
    SoTDependency("DEP-08", "C-02", "C-10", "requires",
                  "The WindowLiftSWC requires vehicle speed to qualify one-touch window operation."),
    SoTDependency("DEP-09", "C-01", "C-07", "requires",
                  "The DoorControlSWC requires keyless authorization status from the KeylessEntrySWC."),
    SoTDependency("DEP-10", "C-11", "C-12", "depends_on",
                  "The RteAdapter depends on the COM module for signal transmission."),
    SoTDependency("DEP-11", "C-12", "C-13", "depends_on",
                  "The COM module depends on the PduR for PDU routing."),
    SoTDependency("DEP-12", "C-13", "C-14", "depends_on",
                  "The PduR depends on the CanIf for CAN transmission."),
    SoTDependency("DEP-13", "C-14", "C-15", "depends_on",
                  "The CanIf depends on the CanDriver for hardware access."),
    SoTDependency("DEP-14", "C-16", "C-15", "requires",
                  "The CanSm requires bus state control of the CanDriver."),
    SoTDependency("DEP-15", "C-02", "C-17", "requires",
                  "The WindowLiftSWC requires NVRAM persistence of anti-pinch thresholds from the NvM."),
    SoTDependency("DEP-16", "C-05", "C-17", "requires",
                  "The SeatAdjustSWC requires NVRAM persistence of memory profiles from the NvM."),
    SoTDependency("DEP-17", "C-18", "C-16", "requires",
                  "The EcuM requires communication channel control from the CanSm during shutdown."),
    SoTDependency("DEP-18", "C-18", "C-12", "requires",
                  "The EcuM requires COM transmission suspension during shutdown."),
    SoTDependency("DEP-19", "C-19", "C-11", "requires",
                  "The DiagManager requires the RteAdapter for diagnostic read access to application data."),
    SoTDependency("DEP-20", "C-01", "C-20", "requires",
                  "The DoorControlSWC requires raw door switch inputs from the IoHwAb."),
    SoTDependency("DEP-21", "C-02", "C-20", "requires",
                  "The WindowLiftSWC requires window motor current feedback from the IoHwAb."),
    SoTDependency("DEP-22", "C-03", "C-20", "requires",
                  "The MirrorAdjustSWC requires mirror motor feedback from the IoHwAb."),
    SoTDependency("DEP-23", "C-04", "C-20", "requires",
                  "The LightControlSWC requires ambient light feedback from the IoHwAb."),
    SoTDependency("DEP-24", "C-05", "C-20", "requires",
                  "The SeatAdjustSWC requires seat motor feedback from the IoHwAb."),
]

FLOWS_V1: list[SoTFlow] = [
    SoTFlow("FL-1", "Door Lock Actuation",
            ["C-20", "C-01", "C-08", "C-11", "C-12", "C-13", "C-14", "C-15"],
            "Driver operates door lock switch",
            "Raw switch input is debounced in IoHwAb, evaluated by DoorControlSWC, arbitrated by BodyControlSWC, and the resulting command is transmitted over CAN."),
    SoTFlow("FL-2", "One-Touch Window Lift with Anti-Pinch",
            ["C-20", "C-02", "C-10", "C-08", "C-11", "C-12", "C-13", "C-14", "C-15"],
            "Driver triggers one-touch window lift",
            "Motor current from IoHwAb is monitored by WindowLiftSWC; vehicle speed from SpeedProviderSWC qualifies the command; BodyControlSWC arbitrates and forwards the request over CAN."),
    SoTFlow("FL-3", "Keyless Unlock",
            ["C-07", "C-08", "C-01", "C-20"],
            "Authenticated key approach detected",
            "KeylessEntrySWC publishes the authentication result; BodyControlSWC authorizes the unlock; DoorControlSWC actuates via IoHwAb."),
    SoTFlow("FL-4", "Seat Memory Recall",
            ["C-17", "C-05", "C-08", "C-20"],
            "Driver selects memory slot",
            "NvM provides the persisted profile, SeatAdjustSWC computes motor targets, BodyControlSWC arbitrates, and IoHwAb drives the seat motors."),
    SoTFlow("FL-5", "Bus-Off Recovery",
            ["C-15", "C-16", "C-18"],
            "CAN controller enters bus-off state",
            "CanDriver reports the fault, CanSm executes the recovery sequence, and EcuM is informed for mode bookkeeping."),
]

REVISION_HISTORY: list[dict[str, str]] = [
    {"version": "1.0.0", "date": "2026-08-14", "author": "R. Sharma",
     "changes": "Initial release of the Adaptive Body Controller HLD."},
    {"version": "1.1.0", "date": "2026-09-08", "author": "R. Sharma",
     "changes": "Architecture update: interface re-plumbing, renames, and consistency corrections."},
]

DOC_TITLE = "Adaptive Body Controller (ABC) — High-Level Design"
DOC_SUBTITLE = "Body & Comfort Domain — AUTOSAR Classic Platform"
DOC_ORG = "Synthetic Demonstration Program (fictional)"
DOC_REF = "ABC-HLD-SYN"
SECURITY_BANNER = "SYNTHETIC SAMPLE — NOT A REAL PROGRAM DOCUMENT"


# ===========================================================================
# HLD v2 — planted defects for analysis / comparison evaluation
# ===========================================================================

DEFECTS: dict[str, dict] = {
    "D1_removed_dependency": {
        "description": "DEP-19 (DiagManager requires RteAdapter) removed in v2 without any replacement path.",
        "kind": "removed_dependency",
    },
    "D2_stale_component_reference": {
        "description": "VehicleModeSWC renamed to VehicleModeMgrSWC (C-09), but IF-12 section still references the old name in one table cell.",
        "kind": "stale_reference",
    },
    "D3_conflicting_provider": {
        "description": "IF-13 VehicleSpeedIF claims a second provider (C-08) in its interface table while section text still says C-10.",
        "kind": "conflicting_provider",
    },
    "D4_orphan_component": {
        "description": "SeatAdjustSWC (C-05) left with no interfaces/dependencies in v2 (all its links removed).",
        "kind": "orphan_component",
    },
    "D5_dropped_signal_referenced": {
        "description": "SG-015 KeyAuthStatus removed from IF-11 in v2, but the IF-11 section text still describes it.",
        "kind": "dropped_signal_reference",
    },
    "D6_contradictory_statement": {
        "description": "v2 text states ClimateInterfaceSWC 'does not require any interface from the BodyControlSWC' while DEP-05 remains listed.",
        "kind": "contradiction",
    },
    "D7_port_count_mismatch": {
        "description": "IF-04 section prose says WindowCommandIF has 2 ports, but its port table lists 3.",
        "kind": "prose_table_mismatch",
    },
    "D8_new_consumer_of_changed_interface": {
        "description": "IF-03 WindowStatusIF gains a new consumer C-04 (LightControlSWC) in v2 — impact-relevant change.",
        "kind": "impact_relevant_change",
    },
}


def build_v1() -> dict:
    """Assemble the v1 source-of-truth bundle."""
    return {
        "version": "1.0.0",
        "revision_history": REVISION_HISTORY[:1],
        "components": COMPONENTS_V1,
        "interfaces": INTERFACES_V1,
        "signals": SIGNALS_V1,
        "dependencies": DEPS_V1,
        "flows": FLOWS_V1,
        "defects": {},
    }


def build_v2() -> dict:
    """
    Assemble the v2 source-of-truth bundle with planted defects applied.

    Defects are applied as *transformations* of the v1 model so that the
    ground-truth diff is derivable mechanically (see ground_truth.py).
    """
    components = [SoTComponent(**c.__dict__) for c in COMPONENTS_V1]
    interfaces = [SoTInterface(
        i.id, i.name, i.kind, i.provider, list(i.consumers)) for i in INTERFACES_V1]
    signals = [SoTSignal(**s.__dict__) for s in SIGNALS_V1]
    deps = [SoTDependency(d.id, d.source_id, d.target_id, d.relationship,
                          d.evidence, d.page_hint) for d in DEPS_V1]
    flows = [SoTFlow(f.id, f.name, list(f.steps), f.trigger, f.description)
             for f in FLOWS_V1]

    # --- D4: orphan SeatAdjustSWC — strip its references minimally:
    # drop C-05 from consumer lists; remove interfaces left with no
    # provider or no consumers; drop dependencies touching C-05.
    removed_iface_ids: set[str] = set()
    for i in interfaces:
        if "C-05" in i.consumers:
            i.consumers = [c for c in i.consumers if c != "C-05"]
        if i.provider == "C-05" or not i.consumers:
            removed_iface_ids.add(i.id)
    interfaces = [i for i in interfaces if i.id not in removed_iface_ids]
    signals = [s for s in signals if s.interface_id in
               {i.id for i in interfaces}]
    deps = [d for d in deps if d.source_id != "C-05" and d.target_id != "C-05"]
    flows = [f for f in flows if f.id != "FL-4"]  # seat flow no longer valid

    # --- D1: remove DEP-19 (DiagManager -> RteAdapter) ---
    deps = [d for d in deps if d.id != "DEP-19"]

    # --- D2: rename C-09; leave one stale table-cell reference ---
    for c in components:
        if c.id == "C-09":
            c.name = "VehicleModeMgrSWC"
            c.description = (
                "Maintains vehicle mode state (park, drive, crash) and "
                "distributes mode changes. Renamed from VehicleModeSWC.")

    # --- D3: conflicting provider on IF-13 (C-08 added alongside C-10) ---
    for i in interfaces:
        if i.id == "IF-13":
            i.provider = "C-08"  # prose will still say C-10 -> conflict

    # --- D5: drop SG-015 from IF-11 (section text will still mention it) ---
    signals = [s for s in signals if s.id != "SG-015"]

    # --- D8: new consumer of IF-03 ---
    for i in interfaces:
        if i.id == "IF-03":
            i.consumers = list(i.consumers) + ["C-04"]

    # D6, D7 are prose-level defects injected by the renderer (v2 flags).

    return {
        "version": "1.1.0",
        "revision_history": REVISION_HISTORY,
        "components": components,
        "interfaces": interfaces,
        "signals": signals,
        "dependencies": deps,
        "flows": flows,
        "defects": DEFECTS,
    }
