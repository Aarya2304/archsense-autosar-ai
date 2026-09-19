"""Document profile detection (M9): evidence-based, never name-only.

Three profiles:

``application_hld``            the synthetic ABC HLD schema (M4 taxonomy:
                               C-/IF-/P-/SG-/DEP-/FL- IDs, catalogue tables,
                               "S-R/C-S interface" prose). Full M4-M7.
``autosar_adaptive_platform``  AUTOSAR Adaptive Platform explanatory
                               documents (ARA / Functional Clusters /
                               Adaptive Platform Foundation/Services). M4
                               structured analysis via the dedicated AUTOSAR
                               extractor; M6/M7 profile-gated.
``generic``                    everything else: M1/M2/M3 full-text + Copilot
                               only; structured analysis is *unavailable*
                               (never fabricated).

Detection requires MULTIPLE independent evidence signals (task Part E: "Do
not classify a document as AUTOSAR merely because 'AUTOSAR' appears in one
sentence"). Each profile defines a signal table; the classifier scores the
signals and requires the configured min-score with at least 2 distinct
signals firing. ``signals_seen`` is returned so the UI/report can show WHY
the profile was chosen (auditability). A manual override is recorded as such
by the pipeline (``manual=True``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------


class DocumentProfile:
    """A named document profile with its extraction capabilities."""

    def __init__(self, name: str, label: str, structured: bool,
                 description: str) -> None:
        self.name = name
        self.label = label
        self.structured = structured
        self.description = description

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"DocumentProfile({self.name!r})"

    def to_dict(self) -> dict:
        return {
            "profile": self.name,
            "label": self.label,
            "structured_extraction": self.structured,
            "description": self.description,
        }


PROFILE_APPLICATION_HLD = DocumentProfile(
    "application_hld", "Application HLD (ABC synthetic schema)",
    structured=True,
    description="Component/Interface/Port/Signal/Dependency/FunctionalFlow "
                "taxonomy with C-/IF-/P-/SG-/DEP-/FL- identifiers.",
)
PROFILE_AUTOSAR = DocumentProfile(
    "autosar_adaptive_platform", "AUTOSAR Adaptive Platform",
    structured=True,
    description="Adaptive Platform architecture vocabulary: ARA, Functional "
                "Clusters, Adaptive Platform Foundation/Services, Machines, "
                "Manifests, Processes.",
)
PROFILE_GENERIC = DocumentProfile(
    "generic", "Generic document", structured=False,
    description="Full-text retrieval and Copilot only; structured "
                "architecture analysis is not available for this profile.",
)

PROFILES: dict[str, DocumentProfile] = {
    p.name: p for p in
    (PROFILE_APPLICATION_HLD, PROFILE_AUTOSAR, PROFILE_GENERIC)
}


def get_profile(name: str) -> DocumentProfile:
    """Resolve a profile by name; unknown names fall back to ``generic``."""
    return PROFILES.get(name, PROFILE_GENERIC)


def profile_name(name: str) -> str:
    return get_profile(name).name


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------


@dataclass
class ProfileDetection:
    """Result of evidence-based profile detection (auditable)."""

    profile: str
    confidence: float
    manual: bool = False
    scores: dict[str, float] = field(default_factory=dict)
    signals_seen: dict[str, list[str]] = field(default_factory=dict)

    @property
    def structured(self) -> bool:
        return get_profile(self.profile).structured

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "label": get_profile(self.profile).label,
            "structured_extraction": self.structured,
            "confidence": round(self.confidence, 3),
            "manual": self.manual,
            "scores": {k: round(v, 2) for k, v in sorted(self.scores.items())},
            "signals_seen": {k: v[:8] for k, v in
                             sorted(self.signals_seen.items())},
        }


def _count(text: str, patterns: dict[str, str]) -> tuple[int, list[str], int]:
    """Total regex hits, distinct labels seen, and matched literals.

    Returns ``(total_hits, distinct_labels, literals)`` — ``distinct_labels``
    drives the multiple-independent-signals requirement; ``literals`` is the
    capped display list for the UI/report.
    """
    total = 0
    labels: list[str] = []
    literals: list[str] = []
    for label, pat in patterns.items():
        for m in re.finditer(pat, text, re.IGNORECASE):
            total += 1
            if label not in labels:
                labels.append(label)
            if len(literals) < 12:
                literals.append(m.group(0)[:60])
    return total, labels, literals


# -- application_hld signals (the synthetic corpus vocabulary) --------------
_HLD_STRUCTURAL = {
    "component_id": r"(?<![\w-])C-\d{2}(?![\w-])",
    "interface_id": r"(?<![\w-])IF-\d{2}(?![\w-])",
    "port_id": r"(?<![\w-])P-\d{3}(?![\w-])",
    "signal_id": r"(?<![\w-])SG-\d{3}(?![\w-])",
    "dependency_id": r"(?<![\w-])DEP-\d{2}(?![\w-])",
    "flow_id": r"(?<![\w-])FL-\d(?![\w-])",
}
_HLD_PROSE = {
    "sr_cs_interface": r"\b(?:S-R|C-S) interface\b",
    "provider_line": r"\bProvider:\s*C-\d{2}",
    "swc_bsw_layer": r"\((?:SWC|BSW|RTE|ECU-Abstraction), [\w -]+? layer\)",
    "functional_flow": r"\bfunctional flow\b",
}

# -- AUTOSAR Adaptive Platform signals (from the real R20-11 document) ------
_AUTOSAR_STRUCTURAL = {
    "doc_id_706": r"Document ID 706",
    "release_tag": r"AUTOSAR AP R(?:1[0-9]|2[0-9])-\d{2}\b",
    "ara_namespace": r"\bara::(?:com|rest|core|per)\b",
    "fc_abbreviation": r"\bFCs?\b",
    "exp_series": r"AUTOSAR_EXP_\w+",
}
_AUTOSAR_PROSE = {
    "ara_full": r"AUTOSAR Runtime for Adaptive",
    "functional_cluster": r"Functional Clusters?",
    "ap_foundation": r"Adaptive Platform Foundation",
    "ap_services": r"Adaptive Platform Services?",
    "adaptive_application": r"Adaptive Applications?",
    "machine_manifest": r"Machine Manifest",
    "execution_manifest": r"Execution [Mm]anifest",
    "service_instance_manifest": r"Service Instance Manifest",
    "pse51": r"\bPSE51\b",
    "state_management": r"State Management",
    "execution_management": r"Execution Management",
    "communication_management": r"Communication Management",
    "platform_health": r"Platform Health Management",
}

# classification thresholds (deliberately strict; tuned against BOTH the
# synthetic corpus and the real AUTOSAR PDF -- see M9 tests)
_MIN_HLD_SCORE = 3.0
_MIN_AUTOSAR_SCORE = 4.0
_MIN_SIGNALS = 2


def detect_profile(pages_text: list[str], tables: list[dict] | None = None,
                   document_name: str = "", manual: str | None = None
                   ) -> ProfileDetection:
    """Classify a document from its page texts (+ optional table records).

    ``pages_text``: cleaned text of each page (order matters, content does
    the talking). ``manual`` forces a profile and is recorded as such.
    """
    if manual:
        return ProfileDetection(profile=get_profile(manual).name,
                                confidence=1.0, manual=True)

    text = "\n".join(pages_text)
    if tables:
        # table content is evidence too; accept dict records (processed
        # JSON) and TableRecord dataclasses (live IngestionResult)
        for t in tables:
            rows = t.get("rows", []) if isinstance(t, dict) \
                else getattr(t, "rows", [])
            for row in rows:
                text += "\n" + " | ".join(str(c) for c in row)

    scores: dict[str, float] = {}
    signals: dict[str, list[str]] = {}

    hld_counts, hld_labels, hld_seen = _count(text, _HLD_STRUCTURAL)
    hld_prose, hld_prose_labels, hld_prose_seen = _count(text, _HLD_PROSE)
    scores["application_hld"] = round(
        min(2.0, hld_counts / 20.0) + min(2.0, hld_prose / 5.0), 2)
    signals["application_hld"] = hld_labels + hld_prose_labels
    literals = {"application_hld": hld_seen + hld_prose_seen}

    au_counts, au_labels, au_seen = _count(text, _AUTOSAR_STRUCTURAL)
    au_prose, au_prose_labels, au_prose_seen = _count(text, _AUTOSAR_PROSE)
    scores["autosar_adaptive_platform"] = round(
        min(2.5, au_counts / 8.0) + min(2.5, au_prose / 12.0), 2)
    signals["autosar_adaptive_platform"] = au_labels + au_prose_labels
    literals["autosar_adaptive_platform"] = au_seen + au_prose_seen

    n_signals_au = len(signals["autosar_adaptive_platform"])
    n_signals_hld = len(signals["application_hld"])

    best = max(scores, key=lambda k: (scores[k], k)) if scores else "generic"

    if best == "application_hld" and scores[best] >= _MIN_HLD_SCORE \
            and n_signals_hld >= _MIN_SIGNALS:
        return ProfileDetection(
            profile=best, confidence=min(1.0, scores[best] / 5.0),
            scores=scores, signals_seen={k: signals[k] + literals[k]
                                         for k in signals})
    if best == "autosar_adaptive_platform" \
            and scores[best] >= _MIN_AUTOSAR_SCORE \
            and n_signals_au >= _MIN_SIGNALS:
        return ProfileDetection(
            profile=best, confidence=min(1.0, scores[best] / 6.0),
            scores=scores, signals_seen={k: signals[k] + literals[k]
                                         for k in signals})

    return ProfileDetection(
        profile="generic", confidence=0.0, scores=scores,
        signals_seen={k: signals[k] + literals[k] for k in signals})
