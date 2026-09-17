"""OpenRouter provider (M3.5).

OpenAI-compatible chat completions at ``OPENROUTER_BASE_URL`` (default
``https://openrouter.ai/api/v1``) with the ``Authorization: Bearer`` key
from the environment. Configuration errors surface as clear ``LLMError``
messages at construction time (fail fast, no confusing stack traces at
query time); the key itself is never logged or included in error strings.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from backend.rag.llm.base import LLMError, LLMResponse, UsageInfo
from backend.rag.llm.http import extract_chat_text, extract_usage, post_chat, \
    redact

# Attribution headers OpenRouter asks clients to send (no secrets).
_SITE_URL = "https://github.com/archsense/archsense"
_APP_TITLE = "ArchSense"


@dataclass
class OpenRouterProvider:
    """Primary cloud provider (OpenAI-compatible endpoint)."""

    api_key: str = ""
    model: str = ""
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 90.0
    max_retries: int = 2
    max_tokens: int = 700
    temperature: float = 0.0        # deterministic-preferred for grounding
    _key_holder: list = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key or not self.api_key.strip():
            raise LLMError(
                "OpenRouter provider requires OPENROUTER_API_KEY. Set it in "
                ".env (never in source code), or choose another provider: "
                "LLM_PROVIDER=ollama (local) or LLM_PROVIDER=mock (offline).")
        if not self.model:
            raise LLMError(
                "OpenRouter provider requires OPENROUTER_MODEL "
                "(e.g. google/gemma-3-27b-it:free).")
        # Hold the key wrapped so accidental repr/logging shows a redacted
        # placeholder instead of the secret.
        self._key_holder = [redact(self.api_key)]

    @property
    def name(self) -> str:
        return "openrouter"

    @property
    def model_name(self) -> str:
        return self.model

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _SITE_URL,
            "X-Title": _APP_TITLE,
        }
        t0 = time.perf_counter()
        data = post_chat(
            url=f"{self.base_url.rstrip('/')}/chat/completions",
            payload=payload, headers=headers,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries, provider=self.name)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        text, finish = extract_chat_text(data, self.name)
        p, c, t = extract_usage(data)
        return LLMResponse(text=text, provider=self.name, model=self.model,
                           latency_ms=latency_ms,
                           usage=UsageInfo(p, c, t), finish_reason=finish,
                           raw=None)  # raw payload intentionally not retained
