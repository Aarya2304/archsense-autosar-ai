"""M3 tests: LLM provider abstraction (mock/OpenRouter/Ollama, mocked).

All tests are offline: OpenRouter/Ollama HTTP paths are exercised through
monkeypatched ``post_chat`` responses; no network, no API keys. Config
failures (missing key) are tested against the real factory.
"""

from __future__ import annotations

import json

import pytest

from backend.rag.llm.base import LLMError, LLMResponse
from backend.rag.llm.factory import get_llm_provider
from backend.rag.llm.http import extract_chat_text, extract_usage, post_chat
from backend.rag.llm.mock import MockLLMProvider


# ------------------------------------------------------------------ mock ---

def test_mock_provider_contract():
    p = MockLLMProvider()
    assert p.name == "mock"
    assert p.model_name == "mock-deterministic"


def test_mock_generates_structured_json_with_citations():
    p = MockLLMProvider()
    user = ("Question: Which component provides VehicleSpeed?\n\n"
            "[EVIDENCE E1]\nDocument: D.pdf\nVersion: 1.0.0\nSection: 4.15\n"
            "Pages: 10\nType: table\nText:\nSpeedProviderSWC provides "
            "VehicleSpeedIF consumed by DoorControlSWC.\n")
    resp = p.generate("sys", user)
    assert isinstance(resp, LLMResponse)
    assert resp.provider == "mock"
    obj = json.loads(resp.text)
    assert set(obj) == {"answer", "evidence_ids", "insufficient_evidence"}
    assert obj["insufficient_evidence"] is False
    assert obj["evidence_ids"] == ["E1"]
    assert "[E1]" in obj["answer"]          # inline citation present
    assert "SpeedProviderSWC" in obj["answer"]


def test_mock_two_blocks_cited_in_order():
    p = MockLLMProvider()
    user = ("Question: VehicleSpeed datatype and consumers?\n\n"
            "[EVIDENCE E1]\nDocument: D.pdf\nVersion: 1.0.0\nSection: 5\n"
            "Pages: 13\nType: table\nText:\nVehicleSpeed is uint16 km/h.\n\n"
            "[EVIDENCE E2]\nDocument: D.pdf\nVersion: 1.0.0\nSection: 4.15\n"
            "Pages: 10\nType: prose\nText:\nVehicleSpeedIF is consumed by "
            "WindowLiftSWC.\n")
    obj = json.loads(p.generate("sys", user).text)
    assert obj["evidence_ids"] == ["E1", "E2"]


def test_mock_force_refusal():
    p = MockLLMProvider(force_refusal=True)
    obj = json.loads(p.generate("sys", "[EVIDENCE E1]\nText:\nsomething").text)
    assert obj["insufficient_evidence"] is True
    assert obj["answer"] == "" and obj["evidence_ids"] == []


def test_mock_fail_with_raises_llm_error():
    p = MockLLMProvider(fail_with="simulated outage")
    with pytest.raises(LLMError, match="simulated outage"):
        p.generate("sys", "[EVIDENCE E1]\nText:\nsomething")


def test_mock_no_evidence_blocks_refuses():
    p = MockLLMProvider()
    obj = json.loads(p.generate("sys", "Question: hi?\nno evidence").text)
    assert obj["insufficient_evidence"] is True


def test_mock_usage_and_latency_populated():
    p = MockLLMProvider()
    resp = p.generate("sys", "Question: q?\n[EVIDENCE E1]\nText:\ncontent")
    assert resp.usage.total_tokens > 0
    assert resp.latency_ms >= 0.0


def test_mock_low_overlap_refuses():
    """Content-token overlap < 0.2 -> structured refusal (U1-style case)."""
    p = MockLLMProvider()
    user = ("Question: What is the brake pressure of the front axle?\n\n"
            "[EVIDENCE E1]\nDocument: D.pdf\nVersion: 1.0.0\nSection: 2\n"
            "Pages: 3\nType: prose\nText:\nBody control functions operate "
            "several comfort features of the vehicle.\n")
    obj = json.loads(p.generate("sys", user).text)
    assert obj["insufficient_evidence"] is True


# -------------------------------------------------------------- factory ---

def test_factory_resolves_mock_by_name():
    assert get_llm_provider("mock").name == "mock"


def test_factory_defaults_to_config_provider(monkeypatch):
    from backend import config
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock", raising=False)
    assert get_llm_provider().name == "mock"


def test_factory_openrouter_without_key_fails_clearly(monkeypatch):
    from backend import config
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "", raising=False)
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        get_llm_provider("openrouter")


def test_factory_unknown_provider(monkeypatch):
    from backend import config
    monkeypatch.setattr(config, "LLM_PROVIDER", "bogus", raising=False)
    with pytest.raises(LLMError, match="Unknown LLM_PROVIDER"):
        get_llm_provider()


# ------------------------------------------------------ openrouter mocked ---

def _patch_post(monkeypatch, payload=None, status=200, body=None,
                calls=None):
    class FakeResponse:
        def __init__(self):
            self.status_code = status
            self.text = body if body is not None else json.dumps(payload)

        def json(self):
            return json.loads(self.text)

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        if calls is not None:
            calls.append({"url": url, "payload": json, "headers": headers})
        return FakeResponse()

    import backend.rag.llm.http as http_mod
    monkeypatch.setattr(http_mod.requests, "post", fake_post)


def test_openrouter_generate_mocked(monkeypatch):
    from backend.rag.llm.openrouter import OpenRouterProvider

    payload = {"choices": [{"message": {"content": "hello"},
                            "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                         "total_tokens": 15}}
    _patch_post(monkeypatch, payload=payload)
    p = OpenRouterProvider(api_key="sk-test", model="m-1")
    resp = p.generate("sys", "user")
    assert resp.text == "hello"
    assert resp.provider == "openrouter"
    assert resp.model == "m-1"
    assert resp.usage.total_tokens == 15
    assert resp.finish_reason == "stop"


def test_openrouter_request_shape(monkeypatch):
    from backend.rag.llm.openrouter import OpenRouterProvider

    calls = []
    _patch_post(monkeypatch, payload={"choices": [{"message": {"content":
                                                  "x"}}]}, calls=calls)
    p = OpenRouterProvider(api_key="sk-test", model="m-1",
                           base_url="https://openrouter.ai/api/v1")
    p.generate("system prompt", "user prompt")
    req = calls[0]
    assert req["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert req["payload"]["model"] == "m-1"
    assert req["payload"]["messages"][0] == {"role": "system",
                                             "content": "system prompt"}
    assert req["payload"]["temperature"] == 0.0
    assert req["headers"]["Authorization"] == "Bearer sk-test"


def test_openrouter_missing_key_raises_at_construction():
    from backend.rag.llm.openrouter import OpenRouterProvider
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        OpenRouterProvider(api_key="", model="m-1")


def test_openrouter_missing_model_raises_at_construction():
    from backend.rag.llm.openrouter import OpenRouterProvider
    with pytest.raises(LLMError, match="OPENROUTER_MODEL"):
        OpenRouterProvider(api_key="sk", model="")


def test_openrouter_http_401_surfaces_clear_error(monkeypatch):
    from backend.rag.llm.openrouter import OpenRouterProvider

    _patch_post(monkeypatch, status=401, body='{"error": "invalid key"}')
    p = OpenRouterProvider(api_key="bad", model="m-1")
    with pytest.raises(LLMError, match="HTTP 401"):
        p.generate("s", "u")


def test_openrouter_no_choices_raises(monkeypatch):
    from backend.rag.llm.openrouter import OpenRouterProvider

    _patch_post(monkeypatch, payload={"choices": []})
    p = OpenRouterProvider(api_key="sk", model="m-1")
    with pytest.raises(LLMError, match="no choices"):
        p.generate("s", "u")


def test_post_chat_retries_on_5xx_then_succeeds(monkeypatch):
    state = {"calls": 0}

    class Resp500:
        status_code = 500
        text = "boom"

        def json(self):
            return {}

    class Resp200:
        status_code = 200
        text = '{"choices": [{"message": {"content": "ok"}}]}'

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    import backend.rag.llm.http as http_mod
    monkeypatch.setattr(http_mod.time, "sleep", lambda s: None)

    def flaky(url, json=None, headers=None, timeout=None, **kw):
        state["calls"] += 1
        return Resp500() if state["calls"] == 1 else Resp200()

    monkeypatch.setattr(http_mod.requests, "post", flaky)
    data = post_chat("http://x", {}, {}, timeout_seconds=1, max_retries=2,
                     provider="test")
    assert data["choices"][0]["message"]["content"] == "ok"
    assert state["calls"] == 2


def test_extract_helpers():
    payload = {"choices": [{"message": {"content": "t"},
                            "finish_reason": "length"}],
               "usage": {"prompt_tokens": 3, "completion_tokens": 4,
                         "total_tokens": 7}}
    assert extract_chat_text(payload, "p") == ("t", "length")
    assert extract_usage(payload) == (3, 4, 7)
    assert extract_usage({}) == (0, 0, 0)


# ----------------------------------------------------------- ollama mocked ---

def test_ollama_generate_mocked(monkeypatch):
    from backend.rag.llm.ollama import OllamaProvider

    _patch_post(monkeypatch, payload={"choices": [{"message": {"content":
                                                  "local answer"}}]})
    p = OllamaProvider(base_url="http://localhost:11434", model="llama3.1:8b")
    resp = p.generate("s", "u")
    assert resp.provider == "ollama"
    assert resp.text == "local answer"


def test_ollama_requires_base_url():
    from backend.rag.llm.ollama import OllamaProvider
    with pytest.raises(LLMError, match="OLLAMA_BASE_URL"):
        OllamaProvider(base_url="", model="m")


def test_ollama_connection_error_is_llm_error(monkeypatch):
    from backend.rag.llm.ollama import OllamaProvider
    import backend.rag.llm.http as http_mod
    import requests as requests_mod

    def refuse(url, json=None, headers=None, timeout=None, **kw):
        raise requests_mod.ConnectionError("server down")

    monkeypatch.setattr(http_mod.requests, "post", refuse)
    p = OllamaProvider(base_url="http://localhost:11434", model="m")
    with pytest.raises(LLMError, match="connection failed"):
        p.generate("s", "u")
