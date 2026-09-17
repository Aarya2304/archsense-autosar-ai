"""Shared HTTP helpers for the real LLM providers (M3.5/M3.6).

Both providers speak the OpenAI-compatible chat-completions JSON format:
OpenRouter natively, Ollama via its ``/v1/chat/completions`` compatibility
endpoint. One ``_post_chat`` helper keeps transport concerns (timeout,
retries, error mapping, auth-header handling, key redaction) in exactly one
place; provider modules stay small and are trivially mockable in tests.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from backend.rag.llm.base import LLMError


class _NoKeyRepr(str):
    """String subclass whose repr/str redacts the secret value."""

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return "***REDACTED***"

    def __str__(self) -> str:
        return "***REDACTED***"


def redact(value: str) -> str:
    """Wrap a secret so that accidental logging can never expose it."""
    return _NoKeyRepr(value)  # type: ignore[return-value]


def post_chat(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
    max_retries: int,
    provider: str,
) -> dict[str, Any]:
    """POST one chat-completion request with bounded retries.

    Retries cover transient conditions only (429/5xx, connection errors).
    4xx (bad key, bad model, bad payload) fail immediately with a clear
    ``LLMError``. Returns the parsed JSON payload; never logs the key.
    """
    last_error: str | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers,
                                 timeout=timeout_seconds)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < max_retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise LLMError(
                f"{provider} connection failed after "
                f"{max_retries + 1} attempt(s): {last_error}") from exc

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise LLMError(
                    f"{provider} returned non-JSON payload") from exc

        if resp.status_code in (429, 500, 502, 503, 504) and \
                attempt < max_retries:
            time.sleep(0.5 * (attempt + 1))
            continue

        # Permanent failure: include status + trimmed body, never headers.
        body = (resp.text or "")[:300].replace("\n", " ")
        raise LLMError(
            f"{provider} HTTP {resp.status_code}: {body}")

    raise LLMError(f"{provider} request failed: {last_error}")  # defensive


def extract_chat_text(payload: dict[str, Any], provider: str) -> tuple[str, str]:
    """Pull (text, finish_reason) out of a chat-completions response."""
    choices = payload.get("choices") or []
    if not choices:
        raise LLMError(f"{provider} response has no choices")
    choice = choices[0]
    message = choice.get("message") or {}
    text = message.get("content")
    if text is None:
        raise LLMError(f"{provider} response missing message.content")
    finish = str(choice.get("finish_reason") or "")
    return str(text), finish


def extract_usage(payload: dict[str, Any]) -> tuple[int, int, int]:
    """Pull (prompt, completion, total) token counts; 0 when absent."""
    usage = payload.get("usage") or {}
    return (int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            int(usage.get("total_tokens") or 0))
