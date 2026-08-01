"""A won deal becomes real: insured + policy into NowCerts.

The agency's rule is that the CRM is the working copy and NowCerts is the record
of what is REAL. A deal is a working copy right up until it is WON — at that
moment the insured and the policy have to exist in the system of record.

Until now they mostly did not. The existing terminal writeback
(``opportunity_writeback``) only fires for opportunities carrying a
``nowcerts_opportunity_id`` — ones that came FROM the AMS. A cross-sell opened in
the CRM on an existing client, or a lead converted here, went won and NowCerts
never heard about it: 50 of 64 opportunities have no such id, and one of the
three won deals on the book is in exactly that state.

This closes it, on the same sanctioned path as every other AMS write — additive,
queued, human-approved, drained by an opt-in executor. Nothing here writes to
NowCerts synchronously.

Two things it will not do:

* **Invent a policy number.** You cannot record a bound policy in the AMS without
  one, and making one up puts junk in the system of record. A won deal with no
  policy number is refused, with a message that says to go and add it.
* **Push a LOST deal.** Nothing about a lost deal is written to NowCerts — it was
  never coverage. It stays in the CRM with its x-date and its lost reason, which
  is next year's remarket list. (A deal mirrored FROM NowCerts still gets its
  stage synced when lost; that record already exists there.)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from hermes.intake import opportunities as opp
from hermes.core.queue import (
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

OBJECT_TYPE = "opportunity_won"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NotPushable(Exception):
    """The deal cannot go to the AMS yet, and a human has to fix something."""


def check_pushable(opportunity: dict[str, Any]) -> None:
    """Raise ``NotPushable`` with something actionable, or return quietly."""
    if str(opportunity.get("status") or "") != opp.STATUS_WON:
        raise NotPushable("only a won deal goes to NowCerts — a lost deal is never pushed")
    if opportunity.get("nowcerts_policy_guid"):
        raise NotPushable("this deal is already in NowCerts")
    if not str(opportunity.get("policy_number") or "").strip():
        raise NotPushable(
            "add the bound policy number to this deal before sending it to NowCerts — "
            "a policy cannot be recorded in the AMS without one"
        )
    if not str(opportunity.get("insured_name") or "").strip():
        raise NotPushable("this deal has no client name to create an insured with")


def stage_won(
    supa: "SupabaseClient",
    opportunity: dict[str, Any],
    *,
    approved_by: str,
) -> dict[str, Any]:
    """Queue the won deal for NowCerts. Raises ``NotPushable`` if it is not ready.

    The payload is a snapshot: the executor writes what was approved, not whatever
    the row happens to say by the time it drains.
    """
    check_pushable(opportunity)
    return supa.insert(
        QUEUE_TABLE,
        {
            "object_type": OBJECT_TYPE,
            "object_id": str(opportunity.get("id")),
            "destination_system": DESTINATION_NOWCERTS,
            "action": "create",
            "payload": {
                "opportunity_id": str(opportunity.get("id")),
                "insured_id": opportunity.get("insured_id"),
                "insured_name": opportunity.get("insured_name"),
                "insured_type": opportunity.get("insured_type"),
                "policy_number": opportunity.get("policy_number"),
                "line_of_business": opportunity.get("line_of_business"),
                "carrier": opportunity.get("carrier"),
                "premium": opportunity.get("premium_actual") or opportunity.get("premium_estimate"),
                "effective_date": opportunity.get("effective_date"),
                "expiration_date": opportunity.get("expiration_date"),
            },
            "status": QUEUE_QUEUED,
            "attempt_count": 0,
            "approved_by": approved_by,
            "approved_at": _utcnow().isoformat(),
        },
    )


def _insured_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """A commercial name, or a person's name split the way NowCerts wants it."""
    name = str(payload.get("insured_name") or "").strip()
    commercial = str(payload.get("insured_type") or "").strip().lower() != "personal"
    if commercial:
        return {"CommercialName": name}
    first, _, last = name.partition(" ")
    return {"FirstName": first, "LastName": last or first}


def _policy_payload(payload: dict[str, Any], insured_guid: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "InsuredDatabaseId": insured_guid,
        "Number": str(payload.get("policy_number") or "").strip(),
        # A won deal is bound coverage, not a quote. Getting this wrong files the
        # policy as a quote and it never counts in the book.
        "IsQuote": False,
    }
    if payload.get("line_of_business"):
        body["LinesOfBusiness"] = payload["line_of_business"]
    if payload.get("carrier"):
        body["CarrierName"] = payload["carrier"]
    if payload.get("premium") is not None:
        body["Premium"] = payload["premium"]
    if payload.get("effective_date"):
        body["EffectiveDate"] = str(payload["effective_date"])[:10]
    if payload.get("expiration_date"):
        body["ExpirationDate"] = str(payload["expiration_date"])[:10]
    return body


def _guid_from(response: Any) -> str | None:
    """Dig the insured GUID out of whatever shape NowCerts answered with."""
    if isinstance(response, dict):
        for key in ("databaseId", "DatabaseId", "id", "Id", "insuredDatabaseId"):
            value = response.get(key)
            if value:
                return str(value)
    if isinstance(response, str) and response.strip():
        return response.strip()
    return None


def _resolve_insured(nowcerts: "NowCertsClient", payload: dict[str, Any]) -> str:
    """The insured's GUID — the one we already had, else one just created.

    An existing client already has an id (that is what makes them a client), so
    this only creates for a deal that started as a prospect or a lead. NowCerts'
    Insured/Insert upserts on the name, so a second run does not mint a duplicate.
    """
    existing = str(payload.get("insured_id") or "").strip()
    if existing:
        return existing
    created = nowcerts.create_insured(_insured_payload(payload))
    guid = _guid_from(created)
    if not guid:
        # Fall back to a lookup: some Insert responses carry no id.
        found = nowcerts.find_insured(commercial_name=payload.get("insured_name")) \
            if hasattr(nowcerts, "find_insured") else None
        guid = _guid_from(found)
    if not guid:
        raise RuntimeError(
            f"NowCerts accepted the insured '{payload.get('insured_name')}' but returned no id"
        )
    return guid


def _eligible(supa: "SupabaseClient", limit: int) -> list[dict[str, Any]]:
    return supa.select(
        QUEUE_TABLE,
        columns="*",
        params={
            "object_type": f"eq.{OBJECT_TYPE}",
            "destination_system": f"eq.{DESTINATION_NOWCERTS}",
            "status": f"eq.{QUEUE_QUEUED}",
            "approved_by": "not.is.null",
            "order": "created_at.asc",
        },
        limit=limit,
    )


def run_opportunity_won_executor(
    *,
    supa: "SupabaseClient | None" = None,
    nowcerts: "NowCertsClient | None" = None,
    limit: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Drain approved won-deal jobs → NowCerts. ``dry_run`` previews only."""
    if supa is None:
        from hermes.integrations.supabase_client import SupabaseClient

        supa = SupabaseClient()
    summary: dict[str, Any] = {"claimed": 0, "completed": 0, "failed": 0, "previews": []}

    for job in _eligible(supa, limit)[:limit]:
        payload = dict(job.get("payload") or {})

        if dry_run:
            summary["previews"].append({
                "queue_id": job.get("id"),
                "opportunity_id": payload.get("opportunity_id"),
                "creates_insured": not payload.get("insured_id"),
                "insured": _insured_payload(payload),
                "policy": _policy_payload(payload, str(payload.get("insured_id") or "<new>")),
            })
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
            insured_guid = _resolve_insured(nowcerts, payload)
            written = nowcerts.insert_policy(_policy_payload(payload, insured_guid))
            policy_guid = _guid_from(written)

            # Link the deal to what it became. insured_id is written too: a
            # converted lead had none until this moment, and without it the client
            # is not cross-sellable next year.
            supa.update("opportunities", payload["opportunity_id"], {
                "insured_id": insured_guid,
                "nowcerts_policy_guid": policy_guid,
                "ams_pushed_at": _utcnow().isoformat(),
            })
            supa.update(QUEUE_TABLE, job.get("id"),
                        {"status": QUEUE_COMPLETED, "updated_at": _utcnow().isoformat()})
            summary["completed"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad job must not kill the run
            log.exception("won-deal push failed on queue_id=%s", job.get("id"))
            supa.update(QUEUE_TABLE, job.get("id"), {
                "status": QUEUE_FAILED,
                "updated_at": _utcnow().isoformat(),
                "last_error": str(exc)[:2000],
            })
            summary["failed"] += 1

    return summary
