"""LLM provider protocol + structured response types (M3.4).

``LLMProvider`` is deliberately tiny: one method, one structured result.
Provider specifics (endpoints, auth, retries, payload formats) stay inside
their modules; callers see only ``LLMResponse``. Nothing here performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class UsageInfo:
    """Token usage as reported by the provider (0 when unknown)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    """Structured provider output (text + provenance metadata)."""

    text: str
    provider: str                    # "mock" | "openrouter" | "ollama"
    model: str
    latency_ms: float
    usage: UsageInfo = UsageInfo()
    finish_reason: str = ""          # provider finish/stop reason, if any
    raw: dict[str, Any] | None = None   # provider payload (never logged)


@runtime_checkable
class LLMProvider(Protocol):
    """Anything that can turn a (system, user) prompt pair into text."""

    @property
    def name(self) -> str:
        """Stable provider identifier (\"mock\"/\"openrouter\"/\"ollama\")."""
        ...

    @property
    def model_name(self) -> str:
        """Model identifier for provenance/audit records."""
        ...

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Generate one completion; must raise LLMError on failure."""
        ...


class LLMError(RuntimeError):
    """Raised for provider failures (config, network, malformed payload).

    Callers are expected to catch this and degrade safely (the copilot
    returns a structured failure, never a fabricated answer).
    """
