"""FindingEngine (M6.10): orchestration — run detectors, validate, summarize.

    AnalysisContext (registry + graph, version-scoped)
        -> detectors (ALL_DETECTORS, or a subset)
        -> deterministic sort
        -> mechanical validation
        -> summary

The engine never persists and never calls an LLM; persistence lives in
``persistence.py`` and is invoked by the service/CLI layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.findings.autosar_detectors import AUTOSAR_DETECTORS
from backend.findings.context import build_analysis_context
from backend.findings.detectors import ALL_DETECTORS
from backend.findings.models import Finding, FindingType
from backend.findings.validator import validate_findings

__all__ = ["EngineResult", "FindingEngine", "DETECTOR_SETS"]

# Profile-aware detector sets (M9): the ABC suite applies to the synthetic
# HLD profile; the AUTOSAR profile gets only detectors meaningful for its
# vocabulary (never manufacture defects; zero findings is a valid result).
DETECTOR_SETS: dict[str, dict] = {
    "application_hld": dict(ALL_DETECTORS),
    "autosar_adaptive_platform": dict(AUTOSAR_DETECTORS),
    "generic": {},                       # no registry -> no detectors
}


@dataclass
class EngineResult:
    findings: list[Finding] = field(default_factory=list)
    version_label: str = ""
    entity_count: int = 0
    fact_count: int = 0
    detectors_run: list[str] = field(default_factory=list)
    validation_ok: bool = True
    validation_issues: list[dict] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def by_type(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.finding_type.value] = out.get(f.finding_type.value, 0) + 1
        return dict(sorted(out.items()))

    def by_severity(self) -> dict[str, int]:
        order = ["high", "medium", "low", "info"]
        out: dict[str, int] = {}
        for f in self.findings:
            k = (f.severity.value if hasattr(f.severity, "value")
                 else str(f.severity))
            out[k] = out.get(k, 0) + 1
        return {k: out[k] for k in order if k in out}


class FindingEngine:
    """Runs the deterministic detector suite over one version.

    ``profile`` selects the detector set (M9); the default keeps the full
    ABC suite so existing callers (M6 tests/CLI/UI) are unaffected. An
    explicit ``detectors`` dict overrides everything.
    """

    def __init__(self, detectors: dict | None = None,
                 profile: str = "application_hld") -> None:
        if detectors is not None:
            self._detectors = dict(detectors)
        else:
            self._detectors = dict(DETECTOR_SETS.get(profile,
                                                     DETECTOR_SETS["generic"]))

    def run(self, session: Session, version_label: str,
            finding_types: list[str] | None = None) -> EngineResult:
        import time
        t0 = time.perf_counter()
        ctx = build_analysis_context(session, version_label)
        t_ctx = time.perf_counter()

        detectors = self._detectors
        if finding_types:
            wanted = set(finding_types)
            detectors = {k: v for k, v in detectors.items() if k in wanted}
        findings: list[Finding] = []
        timings: dict[str, float] = {"context_ms": round((t_ctx - t0) * 1000, 2)}
        for name in sorted(detectors):
            t = time.perf_counter()
            findings.extend(detectors[name](ctx))
            timings[f"detector:{name}_ms"] = round(
                (time.perf_counter() - t) * 1000, 2)

        # deterministic ordering (task rule 18)
        findings.sort(key=lambda f: f.sort_key())

        # mechanical validation against the same trusted inventory
        report = validate_findings(
            findings, known_fact_keys=set(ctx.facts),
            known_entity_keys=set(ctx.entities))
        timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        return EngineResult(
            findings=findings, version_label=ctx.version_label,
            entity_count=ctx.entity_count, fact_count=ctx.fact_count,
            detectors_run=sorted(detectors), validation_ok=report.ok,
            validation_issues=[{"code": i.code, "finding_id": i.finding_id,
                                "detail": i.detail} for i in report.issues],
            timings_ms=timings)

    # convenience: one FindingType filter helper for the CLI
    @staticmethod
    def filter_findings(findings: list[Finding],
                        finding_type: str | None = None,
                        severity: str | None = None) -> list[Finding]:
        out = findings
        if finding_type:
            ft = FindingType(finding_type).value
            out = [f for f in out
                   if (f.finding_type.value
                       if hasattr(f.finding_type, "value")
                       else str(f.finding_type)) == ft]
        if severity:
            sev = severity.lower()
            out = [f for f in out
                   if (f.severity.value if hasattr(f.severity, "value")
                       else str(f.severity)) == sev]
        return out
