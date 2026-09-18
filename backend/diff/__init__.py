"""M7: deterministic revision comparison + impact analysis (backend/diff).

Consumes ONLY trusted M4 registry snapshots + M5 graphs:

    from backend.diff.comparator import RevisionComparator
    cmp = RevisionComparator(db_path).compare("1.0.0", "1.1.0", depth=1)

Deterministic by construction: canonical entity keys (D-021), fact-triple
relationship identity (D-037), content-addressed change/impact IDs, and
sorted output. The LLM is not involved anywhere in the core comparison.
"""

from backend.diff.comparator import RevisionComparator
from backend.diff.models import (ChangeType, ImpactCategory,
                                 RevisionComparison, RevisionFinding,
                                 RevisionSummary)

__all__ = [
    "RevisionComparator",
    "ChangeType",
    "ImpactCategory",
    "RevisionComparison",
    "RevisionFinding",
    "RevisionSummary",
]
