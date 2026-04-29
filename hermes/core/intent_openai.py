"""Optional OpenAI intent fallback for translating plain English to Hermes commands."""

from __future__ import annotations

import os


def _api_key() -> str:
    return os.environ.get("HERMES_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")


def command_from_intent(text: str) -> str | None:
    """Return one Hermes command line, or None when OpenAI is unavailable."""
    api_key = _api_key()
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None

    model = os.environ.get("HERMES_OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key)
    prompt = (
        "Convert the user request into one Hermes command line. "
        "Use only these command shapes: "
        "add contact <name> [email <email>] [to account <account>]; "
        "what is <name> phone; "
        "total premium for <account>; "
        "renewal audit; "
        "cross-sell opportunities. "
        "Return only the command line."
    )
    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            temperature=0,
        )
    except Exception:
        return None
    command = getattr(response, "output_text", "") or ""
    command = command.strip().strip("`").strip()
    return command or None
