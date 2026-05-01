"""Project 85 renewal operations — Supabase-backed renewal tracking."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)

VALID_RISK_STATUSES = ("SAFE", "AT_RISK", "CRITICAL", "RENEWED", "LAPSED")
VALID_ACTION_TYPES = (
    "EMAIL_SENT",
    "SLACK_ALERT",
    "QUOTE_GENERATED",
    "PHONE_CALL",
    "MEETING_SCHEDULED",
    "PROPOSAL_SENT",
    "BOUND",
    "MANUAL_NOTE",
    "RISK_ESCALATION",
)


def upsert_renewal(
    supa: SupabaseClient,
    *,
    policy_number: str,
    client_name: str,
    expiration_date: str,
    premium_current: float | None = None,
    premium_renewal: float | None = None,
    risk_status: str = "SAFE",
    ai_strategy_notes: str | None = None,
    last_contact_date: str | None = None,
) -> dict[str, Any]:
    """Insert or update a Project 85 renewal record."""
    if risk_status not in VALID_RISK_STATUSES:
        raise ValueError(f"Invalid risk_status: {risk_status}; must be one of {VALID_RISK_STATUSES}")

    payload: dict[str, Any] = {
        "policy_number": policy_number,
        "client_name": client_name,
        "expiration_date": expiration_date,
        "risk_status": risk_status,
    }
    if premium_current is not None:
        payload["premium_current"] = premium_current
    if premium_renewal is not None:
        payload["premium_renewal"] = premium_renewal
    if ai_strategy_notes is not None:
        payload["ai_strategy_notes"] = ai_strategy_notes
    if last_contact_date is not None:
        payload["last_contact_date"] = last_contact_date

    return supa.upsert("project_85_renewals", payload, on_conflict="policy_number")


def log_renewal_action(
    supa: SupabaseClient,
    *,
    renewal_id: str,
    action_type: str,
    details: dict[str, Any] | None = None,
    performed_by_role: str = "HermesRenewalSpecialist",
) -> dict[str, Any]:
    """Append an action record to ``renewal_actions``."""
    if action_type not in VALID_ACTION_TYPES:
        raise ValueError(f"Invalid action_type: {action_type}; must be one of {VALID_ACTION_TYPES}")
    return supa.insert(
        "renewal_actions",
        {
            "renewal_id": renewal_id,
            "action_type": action_type,
            "details": details or {},
            "performed_by_role": performed_by_role,
        },
    )


def get_renewals_by_risk(
    supa: SupabaseClient,
    risk_status: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch renewals filtered by risk status."""
    return supa.select(
        "project_85_renewals",
        params={"risk_status": f"eq.{risk_status}"},
        limit=limit,
    )


def get_renewals_expiring_within(
    supa: SupabaseClient,
    days: int,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch renewals expiring within the next N days."""
    from datetime import timedelta
    target = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    return supa.select(
        "project_85_renewals",
        params={
            "and": f"(expiration_date.gte.{target},expiration_date.lte.{cutoff})",
            "order": "expiration_date.asc",
        },
        limit=limit,
    )


def escalate_risk(
    supa: SupabaseClient,
    *,
    renewal_id: str,
    new_status: str,
    reason: str,
    performed_by_role: str = "HermesRenewalSpecialist",
) -> dict[str, Any]:
    """Change a renewal's risk status and log the escalation."""
    if new_status not in VALID_RISK_STATUSES:
        raise ValueError(f"Invalid risk_status: {new_status}; must be one of {VALID_RISK_STATUSES}")

    supa.update(
        "project_85_renewals",
        renewal_id,
        {"risk_status": new_status},
    )

    return log_renewal_action(
        supa,
        renewal_id=renewal_id,
        action_type="RISK_ESCALATION",
        details={"new_status": new_status, "reason": reason},
        performed_by_role=performed_by_role,
    )
