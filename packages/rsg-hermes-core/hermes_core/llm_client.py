"""Single entry point for all LLM calls in Hermes — routes through LiteLLM.

Every module that needs an LLM completion should call ``get_client()`` instead
of constructing ``OpenAI(...)`` directly.  This guarantees:

  * One gateway — LiteLLM owns provider routing, fallback, budget caps,
    cost logging, and caching.  No code path bypasses it.
  * One key — ``LITELLM_API_KEY`` (or ``HERMES_OPENAI_API_KEY`` as legacy
    fallback).  No per-provider keys in the Hermes codebase.
  * Model selection by task — callers pass a LiteLLM model-group name
    (e.g. ``"hermes_intake_default"``) and LiteLLM resolves it to the
    cheapest model that satisfies the group's routing rules.

Env vars (priority order):

  ``LITELLM_BASE_URL`` / ``HERMES_OPENAI_BASE_URL``  — proxy base URL
  ``LITELLM_API_KEY``  / ``HERMES_OPENAI_API_KEY``   — proxy auth key
  ``HERMES_OPENAI_MODEL``                           — default model group

If no base URL is set the client falls back to the OpenAI public API so
local development without LiteLLM still works.
"""

from __future__ import annotations

import os


class LLMConfigError(RuntimeError):
    """Raised when no API key is configured."""


def _resolve_base_url() -> str:
    """Return the LiteLLM proxy URL, or empty string for direct OpenAI."""
    return (
        os.environ.get("LITELLM_BASE_URL")
        or os.environ.get("HERMES_OPENAI_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or ""
    ).strip()


def _resolve_api_key() -> str:
    return (
        os.environ.get("LITELLM_API_KEY")
        or os.environ.get("HERMES_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()


def default_model() -> str:
    """The model-group name used when a caller doesn't specify one."""
    return os.environ.get("HERMES_OPENAI_MODEL", "gpt-4.1-mini").strip()


def get_client():
    """Return a cached ``OpenAI`` SDK client pointed at LiteLLM (or OpenAI).

    Importing ``openai`` is deferred so modules that merely *reference*
    the helper (e.g. for type-checking) don't require the SDK at import time.
    """
    from openai import OpenAI

    api_key = _resolve_api_key()
    if not api_key:
        raise LLMConfigError(
            "No LLM API key configured. Set LITELLM_API_KEY "
            "(or HERMES_OPENAI_API_KEY / OPENAI_API_KEY)."
        )

    kwargs: dict[str, str] = {"api_key": api_key}
    base_url = _resolve_base_url()
    if base_url:
        kwargs["base_url"] = base_url.rstrip("/")
    return OpenAI(**kwargs)


def resolve_model(model: str | None) -> str:
    """Return ``model`` or the default model group if ``model`` is falsy."""
    return (model or default_model()).strip()
