"""Tests for the LLM-agnostic router (hermes/core/llm.py).

Uses an injected config dict + a fake OpenAI-compatible client so no
network, `openai` SDK, or `pyyaml` is required to run these tests.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes.core.llm import BudgetExceeded, LLMRouter, PIIPolicyError, _parse_confidence


def _config() -> dict:
    return {
        "providers": {
            "anthropic": {"base_url": "https://api.anthropic.com/v1", "api_key_env": "ANTHROPIC_API_KEY", "pii_safe": True},
            "google": {"base_url": "https://google", "api_key_env": "GOOGLE_API_KEY", "pii_safe": True},
            "deepseek": {"base_url": "https://deepseek", "api_key_env": "DEEPSEEK_API_KEY", "pii_safe": False},
            "ollama": {"base_url": "http://localhost:11434/v1", "api_key_env": "OLLAMA_API_KEY", "pii_safe": True, "api_key_value": "ollama"},
        },
        "models": {
            "claude-sonnet-5": {"provider": "anthropic", "cost_in": 3.0, "cost_out": 15.0, "pii_safe": True},
            "claude-opus-4-8": {"provider": "anthropic", "cost_in": 15.0, "cost_out": 75.0, "pii_safe": True},
            "gemini-3-1-pro": {"provider": "google", "cost_in": 1.25, "cost_out": 5.0, "pii_safe": True},
            "deepseek-v4-flash": {"provider": "deepseek", "cost_in": 0.14, "cost_out": 0.28, "pii_safe": False},
            "gemma-4": {"provider": "ollama", "cost_in": 0.0, "cost_out": 0.0, "pii_safe": True},
        },
        "task_classes": {
            "standard_drafting": {"default": "claude-sonnet-5", "fallback": "gemini-3-1-pro", "confidence_threshold": 0.75, "max_tokens": 100, "temperature": 0.4},
            "high_volume_classification": {"default": "deepseek-v4-flash", "fallback": "gemini-3-1-pro", "confidence_threshold": 0.80, "max_tokens": 50, "temperature": 0.2},
            "complex_reasoning": {"default": "claude-sonnet-5", "fallback": "gemini-3-1-pro", "escalation": "claude-opus-4-8", "confidence_threshold": 0.75, "max_tokens": 100, "temperature": 0.3},
            "pii_summarization": {"default": "gemma-4", "fallback": "claude-sonnet-5", "confidence_threshold": 0.75, "max_tokens": 100, "temperature": 0.3, "pii": True},
        },
        "agent_budgets": {"renewal-sentinel": 0.10, "service-deflector": 100.0},
    }


@dataclass
class FakeUsage:
    prompt_tokens: int = 100
    completion_tokens: int = 50


class FakeClient:
    """Fake OpenAI-compatible client. ``fail_providers`` forces a failure."""

    def __init__(self, fail_providers=(), content='{"answer":"ok","confidence":0.9}', model_for_content=None):
        self.calls = []
        self._fail = set(fail_providers)
        self._content = content
        self._model_for_content = model_for_content or {}

    class _Completions:
        def __init__(self, parent):
            self.parent = parent

        def create(self, **kwargs):
            return self.parent._create(**kwargs)

    class _Chat:
        def __init__(self, parent):
            self.completions = LLMRouter.__module__ and FakeClient._Completions(parent)

    @property
    def chat(self):
        return FakeClient._Chat(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        provider = kwargs.get("__provider", "")
        if provider in self._fail:
            raise RuntimeError(f"{provider} down")
        model = kwargs["model"]
        content = self._model_for_content.get(model, self._content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=FakeUsage(),
        )


def _factory(client):
    def _f(base_url, api_key):
        client._provider = base_url  # tag so _create knows which provider
        return client

    return _f


def _router(client, **kw):
    # Tag each call with provider via a wrapper client that injects __provider.
    class Wrapped:
        def __init__(self, inner, prov):
            self._inner = inner
            self._prov = prov

        @property
        def chat(self):
            outer = self

            class C:
                @property
                def completions(inner_self):
                    return SimpleNamespace(
                        create=lambda **k: outer._inner._create(**{**k, "__provider": outer._prov})
                    )
            return C()

    mapping = {}

    def factory(base_url, api_key):
        prov = base_url
        mapping.setdefault(prov, Wrapped(client, prov))
        return mapping[prov]

    return LLMRouter(config=_config(), client_factory=factory, **kw)


def test_routes_to_default_model():
    client = FakeClient(content="hello")
    r = _router(client)
    resp = r.complete("standard_drafting", [{"role": "user", "content": "hi"}])
    assert resp.model == "claude-sonnet-5"
    assert resp.content == "hello"
    assert resp.fell_back is False


def test_falls_back_when_primary_fails():
    client = FakeClient(fail_providers={"https://api.anthropic.com/v1"})
    r = _router(client)
    resp = r.complete("standard_drafting", [{"role": "user", "content": "hi"}])
    assert resp.model == "gemini-3-1-pro"
    assert resp.fell_back is True


def test_pii_refuses_non_us_model():
    # high_volume_classification default is deepseek (non-US). With pii=True
    # the router must skip it and use the US fallback (gemini).
    client = FakeClient(content="ok")
    r = _router(client)
    resp = r.complete("high_volume_classification", [{"role": "user", "content": "x"}], pii=True)
    assert resp.model == "gemini-3-1-pro"
    assert resp.fell_back is True


def test_pii_task_with_no_safe_provider_raises():
    client = FakeClient(content="ok")
    r = _router(client)
    # Force both default+fallback to be non-US for this task class.
    r.cfg["task_classes"]["standard_drafting"]["default"] = "deepseek-v4-flash"
    r.cfg["task_classes"]["standard_drafting"]["fallback"] = "deepseek-v4-flash"
    try:
        r.complete("standard_drafting", [{"role": "user", "content": "x"}], pii=True)
        assert False, "expected PIIPolicyError"
    except PIIPolicyError:
        pass


def test_confidence_escalation_to_opus():
    # Primary sonnet returns low confidence -> escalate to opus only.
    primary = '{"answer":"maybe","confidence":0.4}'
    escalated = '{"answer":"better","confidence":0.9}'
    client = FakeClient(model_for_content={"claude-sonnet-5": primary, "claude-opus-4-8": escalated})
    r = _router(client)
    resp = r.complete_with_confidence("complex_reasoning", [{"role": "user", "content": "q"}])
    assert resp.escalated is True
    assert resp.model == "claude-opus-4-8"
    assert resp.confidence == 0.9


def test_no_escalation_when_confident():
    client = FakeClient(content='{"answer":"yes","confidence":0.95}')
    r = _router(client)
    resp = r.complete_with_confidence("complex_reasoning", [{"role": "user", "content": "q"}])
    assert resp.escalated is False
    assert resp.confidence == 0.95


def test_budget_cap_blocks_calls():
    from hermes.core.llm import CostLedger

    client = FakeClient(content="ok")
    ledger = CostLedger()
    ledger.add("renewal-sentinel", 0.50)  # already over the 0.10 monthly cap
    r = _router(client, ledger=ledger)
    try:
        r.complete("standard_drafting", [{"role": "user", "content": "q"}], agent="renewal-sentinel")
        assert False, "expected BudgetExceeded"
    except BudgetExceeded:
        pass


def test_budget_tracks_across_calls():
    from hermes.core.llm import CostLedger

    client = FakeClient(content="ok")
    ledger = CostLedger()
    r = _router(client, ledger=ledger)
    # service-deflector cap is 100; many cheap calls stay under.
    for _ in range(3):
        r.complete("standard_drafting", [{"role": "user", "content": "q"}], agent="service-deflector")
    assert ledger.spent("service-deflector") > 0


def test_parse_confidence_handles_markdown_fence():
    out = _parse_confidence('```json\n{"answer":"hi","confidence":0.8}\n```')
    assert out["answer"] == "hi" and out["confidence"] == 0.8


def test_parse_confidence_handles_trailing_prose():
    out = _parse_confidence('{"answer":"x","confidence":0.6} because reasons')
    assert out["answer"] == "x" and out["confidence"] == 0.6


def test_pii_task_class_flag_routed_to_local():
    client = FakeClient(content="ok")
    r = _router(client)
    resp = r.complete("pii_summarization", [{"role": "user", "content": "secret"}])
    assert resp.model == "gemma-4"  # local, pii_safe
