"""Provider factory (M3.4) — single resolution point from config.

``get_llm_provider()`` maps ``backend.config.LLM_PROVIDER`` to a concrete
provider, applying all timeout/retry/model settings from configuration.
Unknown provider names fail fast with the supported list. Importing this
module never touches the network.
"""

from __future__ import annotations

from backend.rag.llm.base import LLMError, LLMProvider


def get_llm_provider(provider_name: str | None = None) -> LLMProvider:
    """Resolve a provider by name; ``None`` uses ``LLM_PROVIDER`` config.

    Names: ``openrouter`` (requires OPENROUTER_API_KEY), ``ollama`` (local
    server), ``mock`` (deterministic offline; tests/CI/key-less demos).
    """
    from backend import config as cfg

    name = (provider_name or cfg.LLM_PROVIDER or "mock").strip().lower()

    if name == "mock":
        from backend.rag.llm.mock import MockLLMProvider
        return MockLLMProvider()

    if name == "openrouter":
        from backend.rag.llm.openrouter import OpenRouterProvider
        return OpenRouterProvider(
            api_key=cfg.OPENROUTER_API_KEY,
            model=cfg.OPENROUTER_MODEL,
            base_url=cfg.OPENROUTER_BASE_URL,
            timeout_seconds=float(cfg.LLM_TIMEOUT_SECONDS),
            max_retries=cfg.LLM_MAX_RETRIES,
            max_tokens=cfg.LLM_MAX_TOKENS,
        )

    if name == "ollama":
        from backend.rag.llm.ollama import OllamaProvider
        return OllamaProvider(
            base_url=cfg.OLLAMA_BASE_URL,
            model=cfg.OLLAMA_MODEL,
            timeout_seconds=float(cfg.LLM_TIMEOUT_SECONDS),
            max_retries=cfg.LLM_MAX_RETRIES,
            max_tokens=cfg.LLM_MAX_TOKENS,
        )

    raise LLMError(
        f"Unknown LLM_PROVIDER {name!r} (supported: mock, openrouter, "
        f"ollama).")
