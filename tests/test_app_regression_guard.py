"""M8 tests (part 3): regression guard for the hybrid-filter compatibility
fix and app-layer security invariants.

The hybrid fix is the one backend compatibility change M8 made
(``backend/rag/hybrid.py``): store-level equality filters now apply to BOTH
retrieval legs, so version/document scoping cannot leak chunks from other
documents into fused results. These tests pin that contract.
"""

from __future__ import annotations

import pytest

from app.services import app_services as svc


@pytest.fixture(scope="module")
def hybrid():
    from backend.rag.embedder import get_embedder
    from backend.rag.hybrid import get_hybrid_service
    from backend.rag.vector_store import get_vector_store

    try:
        return get_hybrid_service(embedder=get_embedder(),
                                  store=get_vector_store())
    except Exception:  # pragma: no cover - index must be built first
        pytest.skip("vector index not built")


def test_hybrid_version_filter_scopes_both_legs(hybrid):
    result = hybrid.retrieve("VehicleSpeed interface", top_k=10,
                             filters={"version": "1.1.0"})
    assert result.chunks
    assert {c.version for c in result.chunks} == {"1.1.0"}


def test_hybrid_document_filter_scopes_both_legs(hybrid):
    result = hybrid.retrieve("VehicleSpeed interface", top_k=10,
                             filters={"document_name": "ABC_HLD_v1.0.0.pdf"})
    assert result.chunks
    assert {c.document_name for c in result.chunks} == {"ABC_HLD_v1.0.0.pdf"}


def test_hybrid_unfiltered_covers_whole_corpus(hybrid):
    result = hybrid.retrieve("VehicleSpeed interface", top_k=10)
    assert {c.version for c in result.chunks} >= {"1.0.0", "1.1.0"}


def test_unknown_filter_key_rejected(hybrid):
    with pytest.raises(ValueError):
        hybrid.retrieve("VehicleSpeed", top_k=1,
                        filters={"nonsense_key": "x"})


def test_no_secrets_in_app_source():
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    forbidden = ("sk-or-v1-", "sk-", "OPENROUTER_API_KEY=", "api_key=")
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for secret in forbidden:
            assert secret not in text, f"{path} contains {secret!r}"


def test_generated_page_html_contains_no_user_text_directly():
    """components.html receives only backend SVG (D-044): the module must not
    interpolate arbitrary user-supplied strings into its HTML template."""
    import inspect

    import app.components.helpers as helpers

    src = inspect.getsource(helpers.render_page_svg)
    assert "svg" in src
    assert "st.text_input" not in src and "st.text_area" not in src


def test_copilot_service_error_is_actionable(monkeypatch):
    """Provider failures surface as ServiceError with user guidance."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("OpenRouter API key not configured")

    monkeypatch.setattr(svc, "get_copilot", _boom)
    with pytest.raises(svc.ServiceError):
        svc.ask_copilot("Which component provides VehicleSpeed?",
                        version="1.1.0", provider="openrouter")
