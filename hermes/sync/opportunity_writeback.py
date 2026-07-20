"""Opportunity terminal writeback — CRM Bound/Won or Lost → NowCerts (opt-in).

When an opportunity that mirrors a NowCerts opportunity reaches a terminal stage in
the CRM (Bound/Won or Lost), the outcome is pushed back to the AMS. Sanctioned path:
additive, queued, human-approved, drained by an opt-in executor (mirrors the quote/
renewal executors) — nothing writes to NowCerts synchronously.

Stage-only (per RSG): we set ``opportunityStageName``; the disposition is chosen in
the AMS (no public API resolves a disposition name → GUID). The executor re-fetches
the opportunity fresh to round-trip its required fields — so the upsert never blanks
them — and changes only the stage.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from hermes.intake import opportunities as opp
from hermes.renewals.executor import (
    DESTINATION_NOWCERTS,
    QUEUE_COMPLETED,
    QUEUE_FAILED,
    QUEUE_PROCESSING,
    QUEUE_QUEUED,
    QUEUE_TABLE,
)

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient
    from hermes.sync.nowcerts_client import NowCertsClient

log = logging.getLogger(__name__)

OBJECT_TYPE = "opportunity_writeback"

STAGE_BOUND_WON = "Bound / Won"
STAGE_LOST = "Lost"

# OpportunityIntegrationModel write fields to round-trip from a fresh read so the
# InsertOpportunity upsert doesn't blank the required ones. insuredDatabaseId is
# deliberately EXCLUDED: this is always an update (databaseId is set), and re-sending
# the insured makes NowCerts try to re-assign it → 400 "Can't assign to Insured/Prospect".
_WRITE_FIELDS = (
    "lineOfBusinessName", "neededBy", "opportunityStageName", "currentStageDueDate",
    "referralSourceName", "referralSourceContactName", "winProbability", "agencyCommission",
    "assignedTo", "description", "createdFromRenewal", "dispositionDatabaseId", "costOfLead",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def terminal_stage_for(status_or_stage: str | None) -> str | None:
    """Map a CRM won/lost outcome (status or stage string) to the NowCerts terminal
    stage. Returns None when the opportunity is still open."""
    s = str(status_or_stage or "").strip().lower()
    if s == opp.STATUS_WON or "won" in s or "bound" in s:
        return STAGE_BOUND_WON
    if s == opp.STATUS_LOST or "lost" in s or "not renewed" in s:
        return STAGE_LOST
    return None


def stage_writeback(
    supa: "SupabaseClient",
    opportunity: dict[str, Any],
    *,
    approved_by: str,
    stage: str | None = None,
) -> dict[str, Any] | None:
    """Queue an approved opportunity-writeback when a MIRRORED opp goes terminal.

    Returns None (no-op) if the opportunity isn't tied to a NowCerts opportunity id
    or isn't at a terminal stage. Additive + approval-gated like the other executors.
    """
    ncid = str(opportunity.get("nowcerts_opportunity_id") or "").strip()
    if not ncid:
        return None
    target = terminal_stage_for(stage or opportunity.get("stage") or opportunity.get("status"))
    if not target:
        return None
    return supa.insert(
        QUEUE_TABLE,
        {
            "object_type": OBJECT_TYPE,
            "object_id": ncid,
            "destination_system": DESTINATION_NOWCERTS,
            "action": "update",
            "payload": {
                "nowcerts_opportunity_id": ncid,
                "opportunity_id": opportunity.get("id"),
                "target_stage": target,
            },
            "status": QUEUE_QUEUED,
            "attempt_count": 0,
            "approved_by": approved_by,
            "approved_at": _utcnow().isoformat(),
        },
    )


def _writeback_payload(fresh: dict[str, Any], target_stage: str) -> dict[str, Any]:
    payload = {k: fresh.get(k) for k in _WRITE_FIELDS if fresh.get(k) is not None}
    payload["databaseId"] = str(fresh.get("databaseId") or fresh.get("id") or "")
    payload["opportunityStageName"] = target_stage
    # Required-field guards (InsertOpportunity 400s without them).
    a = payload.get("assignedTo")
    payload["assignedTo"] = a if isinstance(a, list) else ([a] if a else [])
    payload.setdefault("winProbability", "Good")
    payload.setdefault("agencyCommission", 0)
    return payload


def _eligible(supa: "SupabaseClient", limit: int) -> list[dict[str, Any]]:
    return supa.select(
        QUEUE_TABLE,
        params={
            "object_type": f"eq.{OBJECT_TYPE}",
            "destination_system": f"eq.{DESTINATION_NOWCERTS}",
            "status": f"eq.{QUEUE_QUEUED}",
            "approved_by": "not.is.null",
            "approved_at": "not.is.null",
            "order": "created_at.asc",
        },
        limit=max(limit, 1),
    )


def run_opportunity_writeback_executor(
    *,
    supa: "SupabaseClient | None" = None,
    nowcerts: "NowCertsClient | None" = None,
    limit: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Drain approved opportunity-writeback jobs → NowCerts. ``dry_run`` previews only."""
    if supa is None:
        from hermes.integrations.supabase_client import SupabaseClient

        supa = SupabaseClient()
    summary: dict[str, Any] = {"claimed": 0, "completed": 0, "failed": 0, "previews": []}

    for job in _eligible(supa, limit)[:limit]:
        payload = dict(job.get("payload") or {})
        ncid = str(payload.get("nowcerts_opportunity_id") or "")
        target = payload.get("target_stage")

        if dry_run:
            summary["previews"].append({"queue_id": job.get("id"), "opportunity": ncid, "target_stage": target})
            continue

        claimed = supa.update_where(
            QUEUE_TABLE,
            {"status": QUEUE_PROCESSING, "updated_at": _utcnow().isoformat()},
            filters={"id": f"eq.{job.get('id')}", "status": f"eq.{QUEUE_QUEUED}"},
        )
        if not claimed:
            continue
        summary["claimed"] += 1

        if nowcerts is None:
            from hermes.sync.nowcerts_client import NowCertsClient

            nowcerts = NowCertsClient()

        try:
            fresh = nowcerts.find_opportunity(ncid)
            if not fresh:
                raise RuntimeError(f"NowCerts opportunity {ncid} not found (deleted?)")
            nowcerts.insert_opportunity(_writeback_payload(fresh, target))
            supa.update(QUEUE_TABLE, job.get("id"), {"status": QUEUE_COMPLETED, "updated_at": _utcnow().isoformat()})
            summary["completed"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad job must not kill the run
            log.exception("opportunity writeback failed on queue_id=%s", job.get("id"))
            supa.update(
                QUEUE_TABLE, job.get("id"),
                {"status": QUEUE_FAILED, "updated_at": _utcnow().isoformat(), "last_error": str(exc)[:2000]},
            )
            summary["failed"] += 1

    return summary
