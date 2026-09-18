"""M6 findings subsystem: deterministic architecture analysis.

    M4 registry + M5 graph (trusted, version-scoped)
        -> detectors (six rule families, no LLM)
        -> mechanical validation
        -> AnalysisRun(kind="analysis") persistence (idempotent)
        -> CLI / evaluation

Public surface:
    FindingEngine           run detectors over one version
    Finding, FindingType    typed model with deterministic IDs
    validate_findings       mechanical validation (V1-V10)
    persist_findings        idempotent storage w/ review preservation
    evaluate_version        ground-truth evaluation (P/R/F1)
"""

from backend.findings.engine import EngineResult, FindingEngine
from backend.findings.models import (EvidenceItem, Finding, FindingType,
                                     deterministic_finding_id)
from backend.findings.validator import FindingIssue, ValidationReport

__all__ = [
    "FindingEngine", "EngineResult", "Finding", "FindingType",
    "EvidenceItem", "deterministic_finding_id", "FindingIssue",
    "ValidationReport",
]
