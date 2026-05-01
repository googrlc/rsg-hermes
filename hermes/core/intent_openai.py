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
        "Use only these command shapes:\n"
        "  add contact <name> [email <email>] [to account <account>]\n"
        "  what is <name> phone\n"
        "  total premium for <account>\n"
        "  renewal audit\n"
        "  cross-sell opportunities\n"
        "  intake <casual description of a meeting or lead>\n"
        "  pipeline\n"
        "  kpi\n"
        "  premium by lob\n"
        "  commission snapshot\n"
        "  stale leads\n"
        "  my accounts\n"
        "  account list\n"
        "  data quality\n"
        "  report personal\n"
        "  bulk normalize\n"
        "  find the <field> for <name> (works for any CRM field: fein, dot number, carrier, policy number, etc.)\n"
        "  find account <name>\n"
        "  find policy <name or number>\n"
        "If the message describes meeting someone, a new lead, or dictating client info, "
        "use 'intake <the original message>'. "
        "If they ask about a specific field on a record, use 'find the <field> for <name>'. "
        "If they ask about data quality, missing fields audit, DQ report, CRM audit, "
        "or cleanliness of CRM data, return exactly 'data quality' "
        "(do not substitute kpi, dashboard, or pipeline). "
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
