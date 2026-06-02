"""Retention save-list builder (Command Center, Phase 2).

Selects the highest-premium at-risk renewals coming due in the next N days and
stages a reviewable outreach DRAFT for each in ``renewal_outreach_drafts``.
Nothing is ever auto-sent — sending stays a manual, human step. Draft bodies are
deterministic templates (no LLM in the request path); Hermes-personalised drafts
can layer on later via the command bar.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

# Only these risk levels make the save-list; sorted by premium (largest first).
SAVE_LIST_RISK = ("CRITICAL", "AT_RISK")


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_lob(policy_number: str | None) -> str | None:
    """RSG policy_number is 'Client | Line of Business | Number' — pull the LOB."""
    if not policy_number or "|" not in policy_number:
        return None
    parts = [p.strip() for p in policy_number.split("|")]
    return parts[1] if len(parts) >= 2 and parts[1] else None


def build_outreach_draft(renewal: dict[str, Any], *, today: date) -> dict[str, Any]:
    """Build a single reviewable outreach draft (status DRAFT) for a renewal."""
    client = (renewal.get("client_name") or "").strip()
    first = client.split()[0] if client else "there"
    lob = parse_lob(renewal.get("policy_number"))
    lob_phrase = f"your {lob} coverage" if lob else "your coverage"
    exp = renewal.get("expiration_date")
    days = renewal.get("days_until")
    when = f"on {exp}" if exp else "soon"
    days_phrase = f" (about {days} days out)" if isinstance(days, int) else ""

    subject = f"{lob + ' renewal' if lob else 'Your policy renewal'} coming up {when} — let's review"
    body = (
        f"Hi {first},\n\n"
        f"{lob_phrase.capitalize()} renews {when}{days_phrase}. I wanted to reach out "
        "personally beforehand to make sure your coverage still fits and to review your "
        "options, so there are no surprises at renewal.\n\n"
        "Do you have 10–15 minutes this week for a quick call? I'll walk you through your "
        "current terms and anything that's changing.\n\n"
        "Best regards,\nRisk Solutions Group"
    )
    return {
        "renewal_id": renewal.get("id"),
        "policy_number": renewal.get("policy_number"),
        "client_name": renewal.get("client_name"),
        "line_of_business": lob,
        "expiration_date": exp,
        "days_until": days if isinstance(days, int) else None,
        "premium_current": _as_float(renewal.get("premium_current")),
        "risk_status": renewal.get("risk_status"),
        "channel": "email",
        "subject": subject,
        "body": body,
        "status": "DRAFT",
    }


def select_save_list(
    renewals: list[dict[str, Any]],
    *,
    today: date,
    limit: int = 10,
    within_days: int = 60,
) -> list[dict[str, Any]]:
    """Pick at-risk renewals due within ``within_days``, highest premium first."""
    enriched: list[dict[str, Any]] = []
    for r in renewals:
        exp = _parse_date(r.get("expiration_date"))
        if exp is None:
            continue
        days = (exp - today).days
        if r.get("risk_status") in SAVE_LIST_RISK and 0 <= days <= within_days:
            enriched.append({**r, "days_until": days})
    enriched.sort(key=lambda r: -(_as_float(r.get("premium_current")) or 0.0))
    return enriched[:limit]


def create_save_list(
    supa: SupabaseClient,
    *,
    limit: int = 10,
    within_days: int = 60,
    today: date | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Build + stage a save-list batch. Returns the batch id and staged drafts."""
    today = today or date.today()
    batch_id = batch_id or str(uuid.uuid4())

    rows = supa.select(
        "project_85_renewals",
        columns="id,policy_number,client_name,expiration_date,premium_current,risk_status",
        limit=2000,
    )
    selected = select_save_list(rows, today=today, limit=limit, within_days=within_days)

    drafts: list[dict[str, Any]] = []
    for r in selected:
        draft = build_outreach_draft(r, today=today)
        draft["batch_id"] = batch_id
        drafts.append(supa.insert("renewal_outreach_drafts", draft))

    log.info("save-list: staged %d draft(s) batch=%s (within=%dd, limit=%d)", len(drafts), batch_id, within_days, limit)
    return {
        "batch_id": batch_id if drafts else None,
        "created": len(drafts),
        "within_days": within_days,
        "drafts": drafts,
    }


def list_open_drafts(supa: SupabaseClient, *, limit: int = 100) -> list[dict[str, Any]]:
    """Current DRAFT outreach awaiting human review/send."""
    return supa.select(
        "renewal_outreach_drafts",
        params={"status": "eq.DRAFT", "order": "premium_current.desc.nullslast"},
        limit=limit,
    )
