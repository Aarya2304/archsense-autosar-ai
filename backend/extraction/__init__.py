"""Structured AUTOSAR entity & fact extraction (M4).

Pipeline (docs/PROJECT_DECISIONS.md D-021..D-024):

    chunks (M2, provenance-carrying)
        -> deterministic extractor (tables + ID/prose patterns)
        -> optional LLM extraction (M3 LLMProvider, evidence-ID based)
        -> mechanical validator (schema / references / evidence / confidence)
        -> normalization + deduplication
        -> SQLite registry (existing M1 typed tables + extraction_facts)
"""

from backend.extraction.models import (EntityType, EvidenceRef,
                                       ExtractedEntity, ExtractedFact,
                                       Predicate, Source, normalize_key)

__all__ = [
    "EntityType", "EvidenceRef", "ExtractedEntity",
    "ExtractedFact", "Predicate", "Source", "normalize_key",
]
