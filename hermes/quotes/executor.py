"""Quote write-back executor — approved opportunity → NowCerts quote (opt-in).

A quote in NowCerts is a Policy with ``IsQuote=true`` (see
``hermes/intake/opportunities.py``). The cockpit enqueues an approval-gated
``outbound_sync_queue`` row (``object_type='quote'``); this executor claims it,
calls NowCerts ``Policy/Insert``, and stamps the quote number + guid back onto the
opportunity. Mirrors the intake/renewal executors exactly:

  * Nothing writes to NowCerts synchronously — the write is queued and human-approved.
  * Guarded claim (queued -> processing) so no double-write.
  * dry_run is side-effect-free (preview the payloads only).
  * Opt-in: run via ``hermes --quote-executor`` (no auto-cron until validated live).
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

OBJECT_TYPE_QUOTE = "quote"
QUOTE_ACTION = "insert_quote"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def map_opportunity_to_quote(o: dict[str, Any]) -> dict[str, Any]:
    """Build a NowCerts Policy/Insert payload (IsQuote=true) from an opportunity.

    Field names match field_mapper.map_commission_to_nowcerts_policy (the proven
    Policy/Insert shape): InsuredDatabaseId, Number, CarrierName,
    LineOfBusinessName, Premium, EffectiveDate, ExpirationDate.
    """
    payload: dict[str, Any] = {"IsQuote": True}
    if o.get("insured_id"):
        payload["InsuredDatabaseId"] = o["insured_id"]
    if o.get("insured_name"):
        payload["InsuredName"] = o["insured_name"]
    if o.get("line_of_business"):
        payload["LineOfBusinessName"] = o["line_of_business"]
    if o.get("carrier"):
        payload["CarrierName"] = o["carrier"]
    if o.get("premium_estimate") is not None:
        try:
            payload["Premium"] = float(o["premium_estimate"])
        except (TypeError, ValueError):
            pass
    if o.get("quote_number"):
        payload["Number"] = o["quote_number"]
    if o.get("effective_date"):
        payload["EffectiveDate"] = str(o["effective_date"])
    if o.get("expiration_date"):
        payload["ExpirationDate"] = str(o["expiration_date"])
    return payload


def stage_quote_job(
    supa: "SupabaseClient", *, opportunity: dict[str, Any], approved_by: str
) -> dict[str, Any]:
    """Enqueue an approved quote write-back for an opportunity.

    Raises ValueError if the opportunity isn't linked to a NowCerts insured — a
    quote must attach to an existing insured (create it via Intake first).
    """
    oid = str(opportunity.get("id") or "")
    if not oid:
        raise ValueError("opportunity id is required")
    if not opportunity.get("insured_id"):
        raise ValueError(
            "opportunity has no insured_id (NowCerts insured GUID) — create/link the insured before quoting"
        )
    return supa.insert(
        QUEUE_TABLE,
        {
            "object_type": OBJECT_TYPE_QUOTE,
            "object_id": oid,
            "destination_system": DESTINATION_NOWCERTS,
            "action": "create",
            "payload": {
                "action": QUOTE_ACTION,
                "opportunity_id": oid,
                "insured_id": opportunity.get("insured_id"),
                "policy": map_opportunity_to_quote(opportunity),
            },
            "status": QUEUE_QUEUED,
            "attempt_count": 0,
            "approved_by": approved_by,
            "approved_at": _utcnow().isoformat(),
        },
    )


def _extract_quote_ref(resp: Any) -> tuple[str | None, str | None]:
    """Pull the quote's databaseId + number from a NowCerts Policy/Insert response
    (the executor reads NowCerts ids from the top level or a nested ``data``)."""
    src: dict[str, Any] = {}
    if isinstance(resp, dict):
        src = resp.get("data") if isinstance(resp.get("data"), dict) else resp
    guid = next((str(src[k]) for k in ("databaseId", "DatabaseId", "id", "Id") if src.get(k)), None)
    number = next((str(src[k]) for k in ("number", "Number", "policyNumber") if src.get(k)), None)
    return guid, number


def _eligible_jobs(supa: "SupabaseClient", limit: int) -> list[dict[str, Any]]:
    return supa.select(
        QUEUE_TABLE, columns="*",
        params={
            "object_type": f"eq.{OBJECT_TYPE_QUOTE}",
            "destination_system": f"eq.{DESTINATION_NOWCERTS}",
            "status": f"eq.{QUEUE_QUEUED}",
            "order": "created_at.asc",
        },
        limit=limit,
    )


def run_quote_executor(
    *,
    supa: "SupabaseClient | None" = None,
    nowcerts: "NowCertsClient | None" = None,
    limit: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Process up to ``limit`` approved quote jobs. ``dry_run`` is side-effect-free."""
    if supa is None:
        from hermes.integrations.supabase_client import SupabaseClient

        supa = SupabaseClient()
    summary: dict[str, Any] = {"claimed": 0, "completed": 0, "failed": 0, "previews": []}

    for job in _eligible_jobs(supa, limit)[:limit]:
        payload = dict(job.get("payload") or {})
        policy = payload.get("policy") or {}
        opp_id = payload.get("opportunity_id")

        if dry_run:
            summary["previews"].append({"queue_id": job.get("id"), "opportunity_id": opp_id, "policy": policy})
            continue

        claimed = supa.update_where(
            QUEUE_TABLE,
            {"status": QUEUE_PROCESSING, "updated_at": _utcnow().isoformat()},
            filters={"id": f"eq.{job.get('id')}", "status": f"eq.{QUEUE_QUEUED}"},
        )
        if not claimed:
            continue  # another cycle grabbed it
        summary["claimed"] += 1

        if nowcerts is None:
            from hermes.sync.nowcerts_client import NowCertsClient

            nowcerts = NowCertsClient()

        try:
            resp = nowcerts.insert_policy(policy)
            guid, number = _extract_quote_ref(resp)
            if not (guid or number):
                raise RuntimeError("NowCerts returned no id/number for the created quote")
            if opp_id:
                try:
                    opp.link_nowcerts(supa, opp_id, quote_number=number, nowcerts_quote_guid=guid)
                except Exception:
                    log.exception("quote: failed to stamp opportunity %s", opp_id)
            supa.update(QUEUE_TABLE, job.get("id"),
                        {"status": QUEUE_COMPLETED, "updated_at": _utcnow().isoformat()})
            summary["completed"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad job must not kill the run
            log.exception("quote executor failed on queue_id=%s", job.get("id"))
            supa.update(QUEUE_TABLE, job.get("id"),
                        {"status": QUEUE_FAILED, "updated_at": _utcnow().isoformat(),
                         "last_error": str(exc)[:2000]})
            summary["failed"] += 1

    return summary
