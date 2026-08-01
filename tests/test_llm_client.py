"""Tests for the shared LLM client (LiteLLM gateway)."""

import importlib
import os
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Wipe LLM-related env vars so each test starts clean."""
    for key in (
        "LITELLM_BASE_URL", "HERMES_OPENAI_BASE_URL", "OPENAI_BASE_URL",
        "LITELLM_API_KEY", "HERMES_OPENAI_API_KEY", "OPENAI_API_KEY",
        "HERMES_OPENAI_MODEL", "HERMES_RESEARCH_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_resolve_base_url_priority(monkeypatch):
    from hermes_core import llm_client
    importlib.reload(llm_client)
    monkeypatch.setenv("LITELLM_BASE_URL", "https://litellm.example.com/v1")
    monkeypatch.setenv("HERMES_OPENAI_BASE_URL", "https://fallback.example.com/v1")
    assert llm_client._resolve_base_url() == "https://litellm.example.com/v1"


def test_resolve_base_url_fallback(monkeypatch):
    from hermes_core import llm_client
    importlib.reload(llm_client)
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.setenv("HERMES_OPENAI_BASE_URL", "https://legacy.example.com/v1")
    assert llm_client._resolve_base_url() == "https://legacy.example.com/v1"


def test_resolve_base_url_empty(monkeypatch):
    from hermes_core import llm_client
    importlib.reload(llm_client)
    assert llm_client._resolve_base_url() == ""


def test_resolve_api_key_priority(monkeypatch):
    from hermes_core import llm_client
    importlib.reload(llm_client)
    monkeypatch.setenv("LITELLM_API_KEY", "sk-litellm")
    monkeypatch.setenv("HERMES_OPENAI_API_KEY", "sk-hermes")
    assert llm_client._resolve_api_key() == "sk-litellm"


def test_resolve_api_key_empty(monkeypatch):
    from hermes_core import llm_client
    importlib.reload(llm_client)
    assert llm_client._resolve_api_key() == ""


def test_default_model(monkeypatch):
    from hermes_core import llm_client
    importlib.reload(llm_client)
    monkeypatch.setenv("HERMES_OPENAI_MODEL", "hermes_intake_default")
    assert llm_client.default_model() == "hermes_intake_default"


def test_default_model_fallback(monkeypatch):
    from hermes_core import llm_client
    importlib.reload(llm_client)
    assert llm_client.default_model() == "gpt-4.1-mini"


def test_resolve_model_explicit(monkeypatch):
    from hermes_core import llm_client
    importlib.reload(llm_client)
    assert llm_client.resolve_model("claude-sonnet") == "claude-sonnet"


def test_resolve_model_falls_back(monkeypatch):
    from hermes_core import llm_client
    importlib.reload(llm_client)
    monkeypatch.setenv("HERMES_OPENAI_MODEL", "deepseek-v4-flash")
    assert llm_client.resolve_model(None) == "deepseek-v4-flash"
    assert llm_client.resolve_model("") == "deepseek-v4-flash"


def test_get_client_no_key_raises(monkeypatch):
    from hermes_core import llm_client
    importlib.reload(llm_client)
    with pytest.raises(llm_client.LLMConfigError):
        llm_client.get_client()


def test_get_client_with_litellm(monkeypatch):
    """Verify the OpenAI SDK client is built with the LiteLLM base_url + key."""
    from hermes_core import llm_client
    importlib.reload(llm_client)
    monkeypatch.setenv("LITELLM_BASE_URL", "https://litellm.example.com/v1")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")

    with mock.patch("openai.OpenAI") as mock_openai:
        llm_client.get_client()
        mock_openai.assert_called_once_with(
            api_key="sk-test",
            base_url="https://litellm.example.com/v1",
        )


def test_get_client_no_base_url(monkeypatch):
    """Without a base URL the client should NOT pass base_url (direct OpenAI)."""
    from hermes_core import llm_client
    importlib.reload(llm_client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-direct")

    with mock.patch("openai.OpenAI") as mock_openai:
        llm_client.get_client()
        mock_openai.assert_called_once_with(api_key="sk-direct")


def test_get_client_strips_trailing_slash(monkeypatch):
    from hermes_core import llm_client
    importlib.reload(llm_client)
    monkeypatch.setenv("LITELLM_BASE_URL", "https://litellm.example.com/v1/")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")

    with mock.patch("openai.OpenAI") as mock_openai:
        llm_client.get_client()
        mock_openai.assert_called_once_with(
            api_key="sk-test",
            base_url="https://litellm.example.com/v1",
        )
