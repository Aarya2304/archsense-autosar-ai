"""LLM provider abstraction (M3.4–M3.6).

The rest of the application depends only on :class:`LLMProvider` and the
structured :class:`LLMResponse` — never on provider SDKs or HTTP details
(D-013 convention extended to generation). Providers:

- ``MockLLMProvider``: deterministic, offline; extracts the answer directly
  from the supplied evidence blocks. Used by tests, CI and key-less demos.
- ``OpenRouterProvider``: OpenAI-compatible chat completions over HTTP
  (``requests``); primary provider.
- ``OllamaProvider``: local Ollama server (optional; not required for the
  normal test suite or the mock demo path).

``get_llm_provider()`` resolves ``backend.config.LLM_PROVIDER`` lazily so
importing this package never requires network access or an API key.
"""

from __future__ import annotations

from backend.rag.llm.base import (LLMError, LLMProvider, LLMResponse,
                                  UsageInfo)
from backend.rag.llm.factory import get_llm_provider

__all__ = [
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "UsageInfo",
    "get_llm_provider",
]
