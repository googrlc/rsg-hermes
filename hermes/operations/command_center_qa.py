"""Renewal facts for the Command Center / Hermes agent.

Provides grounded renewal + at-risk facts from the classified
``project_85_renewals`` data so answers never invent a client, premium, or date.
``renewals_facts`` is the shared retriever used both by the nl_agent
``renewals_overview`` tool (which phrases it conversationally) and by the
no-LLM deterministic fallback in the command-bar endpoint.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Any

from hermes.operations.renewal_tracker import summarize_renewals
from hermes.operations.save_list import parse_lob

log = logging.getLogger(__name__)

RENEWAL_KEYWORDS = ("renew", "expir", "x-date", "xdate", "coming due", "up for renewal")
RISK_KEYWORDS = (
    "at risk", "at-risk", "risk of leaving", "retention", "save list", "save-list",
    "losing", "churn", "lapse", "who's at risk", "whos at risk",
)

_RENEWAL_COLUMNS = (
    "id,policy_number,client_name,expiration_date,premium_current,"
    "premium_renewal,increase_percentage,risk_status,ai_strategy_notes,last_contact_date"
)


def _money(n: Any) -> str:
    try:
        return f"${float(n):,.0f}"
    except (TypeError, ValueError):
        return "$0"


def _window_from_prompt(prompt: str) -> int:
    if "today" in prompt or "tomorrow" in prompt:
        return 1
    if "week" in prompt:
        return 7
    if "month" in prompt:
        return 30
    m = re.search(r"(\d+)\s*day", prompt)
    if m:
        return int(m.group(1))
    return 30


def _fact_line(r: dict[str, Any]) -> str:
    lob = parse_lob(r.get("policy_number"))
    parts = [r.get("client_name") or "Unknown client"]
    if lob:
        parts.append(lob)
    parts.append(_money(r.get("premium_current")) + " premium")
    parts.append(f"renews in {r.get('days_until')} days ({r.get('expiration_date')})")
    parts.append(f"risk {r.get('risk_status') or 'unknown'}")
    if r.get("last_contact_date"):
        parts.append(f"last contact {r.get('last_contact_date')}")
    return " — ".join(parts)


def renewals_facts(
    supa,
    *,
    scope: str = "upcoming",
    within_days: int = 30,
    today: date | None = None,
) -> str:
    """Grounded renewal facts text. scope='upcoming'|'at_risk'."""
    today = today or date.today()
    rows = supa.select("project_85_renewals", columns=_RENEWAL_COLUMNS, limit=2000)
    upcoming = summarize_renewals(rows, today=today)["upcoming"]

    if scope == "at_risk":
        items = sorted(
            (r for r in upcoming if r.get("risk_status") in ("CRITICAL", "AT_RISK")),
            key=lambda r: -(r.get("premium_current") or 0.0),
        )
        label = "at-risk clients with renewals in the next 90 days"
    else:
        items = [r for r in upcoming if r.get("days_until") is not None and r["days_until"] <= within_days]
        items.sort(key=lambda r: (r["days_until"], -(r.get("premium_current") or 0.0)))
        label = f"renewals due in the next {within_days} days"

    items = items[:20]
    total = sum(r.get("premium_current") or 0.0 for r in items)
    if not items:
        return f"There are no {label}."
    lines = "\n".join(f"- {_fact_line(r)}" for r in items)
    return f"{len(items)} {label} ({_money(total)} premium at stake):\n{lines}"


def is_renewal_intent(prompt: str) -> bool:
    """True when the prompt is about renewals or at-risk/retention clients."""
    p = (prompt or "").lower()
    return any(k in p for k in RENEWAL_KEYWORDS) or any(k in p for k in RISK_KEYWORDS)


def _retrieve(supa, prompt: str, today: date) -> str | None:
    p = (prompt or "").lower()
    is_renewal = any(k in p for k in RENEWAL_KEYWORDS)
    is_risk = any(k in p for k in RISK_KEYWORDS)
    if not (is_renewal or is_risk):
        return None
    if is_risk and not is_renewal:
        return renewals_facts(supa, scope="at_risk", today=today)
    return renewals_facts(supa, scope="upcoming", within_days=_window_from_prompt(p), today=today)


def _llm_answer(prompt: str, context: str) -> str | None:
    """Phrase a warm, grounded answer with the LLM. None if unavailable."""
    api_key = os.environ.get("HERMES_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI

        model = os.environ.get("HERMES_OPENAI_MODEL", "gpt-4.1-mini")
        resp = OpenAI(api_key=api_key).chat.completions.create(
            model=model,
            temperature=0.4,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Hermes, the warm, sharp right hand to Lamar at Risk Solutions "
                        "Group, an insurance agency. Talk like a real account manager, not a "
                        "database. Use ONLY the data provided — never invent clients, premiums, "
                        "or dates. Lead with who to contact first and why; give a concrete next "
                        "step per client. Be concise and human."
                    ),
                },
                {"role": "user", "content": f"{prompt}\n\nLive renewal data:\n{context}"},
            ],
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception:
        log.exception("command-center LLM phrasing failed; using grounded fallback")
        return None


def answer_question(supa, prompt: str, *, today: date | None = None, use_llm: bool = True) -> str | None:
    """Deterministic-fallback path: grounded renewal/at-risk answer, else None."""
    today = today or date.today()
    facts = _retrieve(supa, prompt, today)
    if facts is None:
        return None
    if use_llm:
        phrased = _llm_answer(prompt, facts)
        if phrased:
            return phrased
    return facts
