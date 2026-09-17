"""Ollama provider (M3.6) — optional local fallback.

Talks to a local Ollama server's OpenAI-compatible endpoint
(``{OLLAMA_BASE_URL}/v1/chat/completions``). Nothing here requires Ollama
to be running at import or test time: connection errors surface as clear
``LLMError`` messages only when ``generate`` is actually called, and the
project's tests/demo path never require a live server (mock provider or
OpenRouter cover that). No extra dependencies beyond ``requests``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from backend.rag.llm.base import LLMError, LLMResponse, UsageInfo
from backend.rag.llm.http import extract_chat_text, extract_usage, post_chat


@dataclass
class OllamaProvider:
    """Local-model provider (no API key; server must be running to answer)."""

    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    timeout_seconds: float = 120.0
    max_retries: int = 0            # local server: fail fast, no backoff
    max_tokens: int = 700
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if not self.base_url:
            raise LLMError(
                "Ollama provider requires OLLAMA_BASE_URL "
                "(default http://localhost:11434).")
        if not self.model:
            raise LLMError(
                "Ollama provider requires OLLAMA_MODEL (e.g. llama3.1:8b).")

    @property
    def name(self) -> str:
        return "ollama"

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
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        t0 = time.perf_counter()
        data = post_chat(
            url=f"{self.base_url.rstrip('/')}/v1/chat/completions",
            payload=payload, headers=headers,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries, provider=self.name)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        text, finish = extract_chat_text(data, self.name)
        p, c, t = extract_usage(data)
        return LLMResponse(text=text, provider=self.name, model=self.model,
                           latency_ms=latency_ms,
                           usage=UsageInfo(p, c, t), finish_reason=finish)
