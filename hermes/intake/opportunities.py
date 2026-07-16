"""Opportunities pipeline — the Supabase-native sales pipeline for new business.

Grounded in NowCerts' real vocab (live read-only probe): prospect_type is
Prospect/Hot_Prospect/Cold_Prospect, insured_type is Personal/Commercial, and a
quote is a Policy row with isQuote=true (quote_number + nowcerts_quote_guid).
NowCerts' own quote stages are barely used, so the stage/next-action process
lives here and is mirrored to NowCerts on write.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

TABLE = "opportunities"

# Pipeline stages (ordered). Bound = won, Lost = lost, everything else open.
STAGE_NEW = "New"
STAGE_INFO = "Info Gathering"
STAGE_QUOTING = "Quoting"
STAGE_QUOTED = "Quoted"
STAGE_BOUND = "Bound"
STAGE_LOST = "Lost"
STAGES = (STAGE_NEW, STAGE_INFO, STAGE_QUOTING, STAGE_QUOTED, STAGE_BOUND, STAGE_LOST)

# NowCerts prospect_type / insured_type vocab (observed live).
PROSPECT_TYPES = ("Prospect", "Hot_Prospect", "Cold_Prospect")
INSURED_TYPES = ("Personal", "Commercial")

STATUS_OPEN = "open"
STATUS_WON = "won"
STATUS_LOST = "lost"


def status_for_stage(stage: str) -> str:
    if stage == STAGE_BOUND:
        return STATUS_WON
    if stage == STAGE_LOST:
        return STATUS_LOST
    return STATUS_OPEN


def make_client_identifier(name: str | None, fein: str | None = None) -> str:
    """Stable idempotency key from a client name (+ FEIN when present)."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    digits = re.sub(r"\D", "", str(fein or ""))
    if digits:
        return f"{base or 'unknown'}:{digits}"
    return base or "unknown"


def create_opportunity(
    supa: "SupabaseClient",
    *,
    client_identifier: str,
    line_of_business: str,
    insured_name: str | None = None,
    insured_id: str | None = None,
    prospect_type: str | None = None,
    insured_type: str | None = None,
    stage: str = STAGE_NEW,
    premium_estimate: float | None = None,
    carrier: str | None = None,
    lead_source: str | None = None,
    assigned_to: str | None = None,
    next_action: str | None = None,
    description: str | None = None,
    probability: int | None = None,
    source: str | None = None,
    created_by: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Idempotent per (client_identifier, line_of_business). Returns (row, created)."""
    if stage not in STAGES:
        raise ValueError(f"Unknown stage '{stage}'; must be one of {list(STAGES)}")
    if not client_identifier or not line_of_business:
        raise ValueError("client_identifier and line_of_business are required")

    existing = supa.select(
        TABLE,
        columns="*",
        params={
            "client_identifier": f"eq.{client_identifier}",
            "line_of_business": f"eq.{line_of_business}",
        },
        limit=1,
    )
    if existing:
        return existing[0], False

    row = supa.insert(
        TABLE,
        {
            "client_identifier": client_identifier,
            "line_of_business": line_of_business,
            "insured_name": insured_name,
            "insured_id": insured_id,
            "prospect_type": prospect_type,
            "insured_type": insured_type,
            "stage": stage,
            "status": status_for_stage(stage),
            "premium_estimate": premium_estimate,
            "carrier": carrier,
            "lead_source": lead_source,
            "assigned_to": assigned_to,
            "next_action": next_action,
            "description": description,
            "probability": probability,
            "source": source,
            "created_by": created_by,
        },
    )
    return row, True


def advance_stage(
    supa: "SupabaseClient",
    opportunity_id: str,
    stage: str,
    *,
    lost_reason: str | None = None,
) -> dict[str, Any]:
    """Move an opportunity to *stage*, syncing status (Bound→won, Lost→lost)."""
    if stage not in STAGES:
        raise ValueError(f"Unknown stage '{stage}'; must be one of {list(STAGES)}")
    payload: dict[str, Any] = {"stage": stage, "status": status_for_stage(stage)}
    if stage == STAGE_LOST and lost_reason:
        payload["lost_reason"] = lost_reason
    return supa.update(TABLE, opportunity_id, payload)


def link_nowcerts(
    supa: "SupabaseClient",
    opportunity_id: str,
    *,
    insured_id: str | None = None,
    quote_number: str | None = None,
    nowcerts_quote_guid: str | None = None,
) -> dict[str, Any]:
    """Backfill NowCerts identifiers once the insured/quote is created."""
    payload = {
        k: v
        for k, v in {
            "insured_id": insured_id,
            "quote_number": quote_number,
            "nowcerts_quote_guid": nowcerts_quote_guid,
        }.items()
        if v is not None
    }
    if not payload:
        return {}
    return supa.update(TABLE, opportunity_id, payload)


def list_opportunities(
    supa: "SupabaseClient",
    *,
    stage: str | None = None,
    status: str | None = STATUS_OPEN,
    assigned_to: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {"order": "updated_at.desc"}
    if stage:
        params["stage"] = f"eq.{stage}"
    if status:
        params["status"] = f"eq.{status}"
    if assigned_to:
        params["assigned_to"] = f"eq.{assigned_to}"
    return supa.select(TABLE, columns="*", params=params, limit=limit)
