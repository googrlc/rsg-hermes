"""Command Center Q&A intent layer (Phase 3).

The generic dispatcher treats free text as an entity lookup, so renewal questions
like "who renews this week?" fall through to a useless name search. This module
answers the Command Center's core questions (renewals, at-risk/retention) directly
from the classified ``project_85_renewals`` data, and returns None for anything it
doesn't recognise so the caller can fall back to the dispatcher.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from hermes.operations.renewal_tracker import summarize_renewals
from hermes.operations.save_list import parse_lob

RENEWAL_KEYWORDS = ("renew", "expir", "x-date", "xdate", "coming due", "up for renewal")
RISK_KEYWORDS = (
    "at risk", "at-risk", "risk of leaving", "retention", "save list", "save-list",
    "losing", "churn", "lapse", "who's at risk", "whos at risk",
)

ACTION_BY_RISK = {
    "CRITICAL": "call today — at/over renewal date",
    "AT_RISK": "reach out this week to lock the renewal",
    "RENEWED": "renewed — confirm bind & close out",
    "LAPSED": "win-back call",
    "SAFE": "on track — monitor",
}

_RENEWAL_COLUMNS = (
    "id,policy_number,client_name,expiration_date,premium_current,"
    "premium_renewal,increase_percentage,risk_status,ai_strategy_notes,last_contact_date"
)


def _money(n: Any) -> str:
    try:
        return f"${float(n):,.0f}"
    except (TypeError, ValueError):
        return "$—"


def _window_from_prompt(prompt: str) -> tuple[int, str]:
    """Return (days, label) for the time window referenced in the prompt."""
    if "today" in prompt or "tomorrow" in prompt:
        return 1, "today"
    if "week" in prompt:
        return 7, "the next 7 days"
    if "month" in prompt:
        return 30, "the next 30 days"
    m = re.search(r"(\d+)\s*day", prompt)
    if m:
        n = int(m.group(1))
        return n, f"the next {n} days"
    return 30, "the next 30 days"


def _fmt_row(r: dict[str, Any]) -> str:
    lob = parse_lob(r.get("policy_number"))
    who = r.get("client_name") or "Unknown client"
    label = f"{who}" + (f" · {lob}" if lob else "")
    risk = r.get("risk_status") or "—"
    action = ACTION_BY_RISK.get(risk, "review")
    return f"• {label} — {_money(r.get('premium_current'))} — {r.get('days_until')}d — {risk}: {action}"


def answer_question(supa, prompt: str, *, today: date | None = None) -> str | None:
    """Answer renewal/at-risk questions from live data, else None."""
    today = today or date.today()
    p = (prompt or "").lower()
    is_renewal = any(k in p for k in RENEWAL_KEYWORDS)
    is_risk = any(k in p for k in RISK_KEYWORDS)
    if not (is_renewal or is_risk):
        return None

    rows = supa.select("project_85_renewals", columns=_RENEWAL_COLUMNS, limit=2000)
    summ = summarize_renewals(rows, today=today)
    upcoming = summ["upcoming"]

    if is_risk and not is_renewal:
        items = sorted(
            (r for r in upcoming if r.get("risk_status") in ("CRITICAL", "AT_RISK")),
            key=lambda r: -(r.get("premium_current") or 0.0),
        )
        if not items:
            return "No CRITICAL or AT_RISK renewals are coming due in the next 90 days. 🎉"
        total = sum(r.get("premium_current") or 0.0 for r in items)
        head = f"{len(items)} at-risk renewals in the next 90 days ({_money(total)} premium), biggest first:"
        return head + "\n" + "\n".join(_fmt_row(r) for r in items[:15])

    # renewal question (optionally with a time window)
    days, label = _window_from_prompt(p)
    items = [r for r in upcoming if r.get("days_until") is not None and r["days_until"] <= days]
    items.sort(key=lambda r: (r["days_until"], -(r.get("premium_current") or 0.0)))
    if not items:
        return f"No policies renew in {label}. Next up beyond that: {summ['upcoming_count']} within 90 days."
    total = sum(r.get("premium_current") or 0.0 for r in items)
    head = f"{len(items)} policies renew in {label} ({_money(total)} premium), most urgent first:"
    return head + "\n" + "\n".join(_fmt_row(r) for r in items[:15])
