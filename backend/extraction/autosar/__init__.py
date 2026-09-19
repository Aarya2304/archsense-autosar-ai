"""AUTOSAR extraction subpackage (M9)."""

from backend.extraction.autosar.extractor import (AutosarExtraction,
                                                  extract_autosar)
from backend.extraction.autosar.models import (AutosarEntity, AutosarEntityType,
                                               AutosarFact, AutosarPredicate,
                                               AutosarSource, normalize_key)
from backend.extraction.autosar.persistence import (begin_run, clear_registry,
                                                    finish_run,
                                                    persist_extraction)
from backend.extraction.autosar.validate import (AutosarIssue,
                                                 ValidatedAutosarExtraction,
                                                 validate_autosar_extraction)

__all__ = [
    "AutosarEntity", "AutosarEntityType", "AutosarFact", "AutosarPredicate",
    "AutosarSource", "AutosarExtraction", "extract_autosar", "normalize_key",
    "begin_run", "finish_run", "clear_registry", "persist_extraction",
    "AutosarIssue", "ValidatedAutosarExtraction",
    "validate_autosar_extraction",
]
