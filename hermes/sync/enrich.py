"""Single-account syncback: push an ACTIVE EspoCRM Account's data back to
NowCerts to enrich the existing insured.

Safety design (writes to the AMS — the system of record):
  * Only accounts with ``lifecycle_status == "Active"`` are pushed.
  * Only accounts already linked to NowCerts (``momentum_client_id`` set) are
    pushed, and the payload includes that id as ``DatabaseId`` so NowCerts
    ``/api/Insured/Insert`` *upserts the existing insured* — it can never
    create a new insured from CRM data.

Used by the EspoCRM webhook syncback (``/api/hermes/nowcerts-enrich``) and by
the ``hermes --enrich-nowcerts`` CLI for dry-run / single-account testing.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from hermes.sync.field_mapper import map_account_to_insured

if TYPE_CHECKING:
    from hermes.core.client import EspoClient
    from hermes.sync.nowcerts_client import NowCertsClient

log = logging.getLogger(__name__)

ACTIVE_STATUS = "Active"


def enrich_insured_from_account(
    espo: "EspoClient",
    nc: "NowCertsClient",
    account_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Enrich one NowCerts insured from its EspoCRM Account.

    Returns a result dict with ``action`` in
    {skip, would_enrich, enriched, error} and supporting detail. Never raises.
    """
    result: dict[str, Any] = {"account_id": account_id, "dry_run": dry_run, "ok": False, "action": None}

    try:
        acct = espo.get(f"Account/{account_id}")
    except Exception as exc:  # network / 404 / auth
        result["action"] = "error"
        result["error"] = f"account fetch failed: {exc}"
        log.warning("enrich: account %s fetch failed: %s", account_id, exc)
        return result

    if not isinstance(acct, dict) or not acct.get("id"):
        result["action"] = "skip"
        result["reason"] = "account not found"
        return result

    status = acct.get("lifecycle_status")
    if status != ACTIVE_STATUS:
        result["action"] = "skip"
        result["reason"] = f"lifecycle_status={status!r} (only Active is synced)"
        return result

    mcid = str(acct.get("momentum_client_id") or "").strip()
    if not mcid:
        result["action"] = "skip"
        result["reason"] = "no momentum_client_id (account not linked to a NowCerts insured)"
        return result

    payload = map_account_to_insured(acct, nowcerts_database_id=mcid)
    result["nowcerts_database_id"] = mcid
    result["payload_fields"] = sorted(payload.keys())

    if dry_run:
        result["ok"] = True
        result["action"] = "would_enrich"
        result["payload"] = payload
        log.info("enrich DRY RUN: would upsert NowCerts insured %s from account %s (%d fields)",
                 mcid, account_id, len(payload))
        return result

    try:
        resp = nc.create_insured(payload)  # Insured/Insert upserts on DatabaseId
    except Exception as exc:
        result["action"] = "error"
        result["error"] = f"NowCerts enrich failed: {exc}"
        log.warning("enrich: NowCerts upsert failed for %s (acct %s): %s", mcid, account_id, exc)
        return result

    result["ok"] = True
    result["action"] = "enriched"
    result["nowcerts_response"] = resp
    log.info("enrich: upserted NowCerts insured %s from account %s", mcid, account_id)
    return result
