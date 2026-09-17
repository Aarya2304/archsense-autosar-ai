"""Backend graph subsystem (M5): NetworkX MultiDiGraph derived from the
M4 extraction registry (D-026) with trusted per-edge provenance (D-028),
version scoping (D-029), analysis, filtering, JSON export and pyvis
rendering. UI-independent by design; the M4 registry stays the single
source of truth.
"""

from backend.graph.models import (ATTR_CONFIDENCE, ATTR_FACT_KEY,
                                  ATTR_NAME, ATTR_NORM, ATTR_OBJECT_VALUE,
                                  ATTR_PREDICATE, ATTR_PROVENANCE,
                                  ATTR_SOURCE, ATTR_TYPE, ATTR_VERSION,
                                  GraphEdge, GraphStatistics,
                                  GraphValidationResult, Provenance)

__all__ = [
    "ATTR_CONFIDENCE", "ATTR_FACT_KEY", "ATTR_NAME", "ATTR_NORM",
    "ATTR_OBJECT_VALUE", "ATTR_PREDICATE", "ATTR_PROVENANCE", "ATTR_SOURCE",
    "ATTR_TYPE", "ATTR_VERSION", "GraphEdge", "GraphStatistics",
    "GraphValidationResult", "Provenance",
]
