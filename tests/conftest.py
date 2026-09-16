"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def dataset(tmp_path_factory):
    """Build the synthetic dataset once per test session.

    PDFs are rendered into a temp dir copy? No - the canonical dataset is
    regenerated in the standard location (data/) once per session, and
    tests that need isolation copy artifacts they mutate.
    """
    from backend.dataset.ground_truth import build_all

    return build_all(force=True)
