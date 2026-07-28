"""Leads — live NowCerts prospects (insureds carrying a ``prospectType``).

Read-only feed for the cockpit Leads list. Leads do NOT auto-populate the
pipeline; a lead is promoted by creating an opportunity (POST /api/opportunities)
from the selected insured. Sourced live from NowCerts per the approved design.

Perf note: NowCerts has no reliable ``$filter`` on prospectType, so this fetches
the insured list and filters client-side. Fine for the agency book size; if it
grows, cache into the canonical mirror (add prospectType to canonical_book_sync).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# prospectType (Hot/Cold/Prospect) is a lead TEMPERATURE and is often blank on a
# NowCerts prospect. NowCerts actually marks prospect-vs-customer with the connector
# ``type`` code (1 = prospect, 0 = insured) — see hermes/intake/nowcerts_map. Detect a
# lead by EITHER signal so prospects created directly in the AMS (no temperature) show.
_PROSPECT_TYPE_VALUES = {"prospect", "hot_prospect", "cold_prospect", "hot prospect", "cold prospect"}
_PROSPECT_CODES = {"1", "prospect"}


def _is_prospect(ins: dict[str, Any]) -> bool:
    if str(ins.get("prospectType") or "").strip().lower() in _PROSPECT_TYPE_VALUES:
        return True
    if str(ins.get("type") or "").strip().lower() in _PROSPECT_CODES:
        return True
    p = ins.get("prospect")
    return p is True or str(p or "").strip().lower() == "true"


def _name(ins: dict[str, Any]) -> str:
    commercial = ins.get("commercialName") or ins.get("insuredCommercialName")
    if commercial:
        return str(commercial).strip()
    parts = [str(ins.get("firstName") or "").strip(), str(ins.get("lastName") or "").strip()]
    return " ".join(p for p in parts if p).strip()


def _map_lead(ins: dict[str, Any]) -> dict[str, Any]:
    return {
        "insured_id": str(ins.get("id") or ins.get("databaseId") or "").strip() or None,
        "name": _name(ins),
        "prospect_type": ins.get("prospectType"),
        "insured_type": ins.get("insuredType"),
        "email": ins.get("eMail") or ins.get("email"),
        "phone": ins.get("phone") or ins.get("cellPhone"),
        "lead_source": ins.get("leadSources") or ins.get("referralSourceName"),
        # city was missing while the Leads list has always had a City column, so
        # every row rendered it blank.
        "city": ins.get("city"),
        "state": ins.get("state"),
    }


def _attach_x_dates(supa: Any, leads: list[dict[str, Any]]) -> None:
    """Hang each lead's x-date on it, in place.

    The x-date — when the coverage we are chasing expires — is the only date that
    ranks a lead list, and a NowCerts insured record does not carry one: it is a
    property of the policy they hold today, not of the person. Ours comes off the
    opportunity/quote mirror, which the quote sync fills from the NowCerts quote
    (``expiration_date``), keyed on the insured GUID.

    Soonest wins when a lead has several open lines — that is the one with a
    deadline. A lead with nothing in the pipeline has no x-date to show, and is
    left blank rather than given a guess.
    """
    by_insured = {str(lead.get("insured_id") or ""): lead for lead in leads if lead.get("insured_id")}
    if not by_insured:
        return
    try:
        rows = supa.select(
            "opportunities",
            columns="insured_id,line_of_business,expiration_date,stage",
            params={
                "insured_id": f"in.({','.join(by_insured)})",
                "status": "eq.open",
                "expiration_date": "not.is.null",
                "order": "expiration_date.asc",
            },
            limit=1000,
        )
    except Exception:  # noqa: BLE001 — a missing x-date must not empty the Leads list
        log.exception("leads: x-date lookup failed")
        return

    for row in rows:
        lead = by_insured.get(str(row.get("insured_id") or ""))
        # Ordered by expiration ascending, so the first row for a lead is the soonest.
        if lead is None or lead.get("x_date"):
            continue
        lead["x_date"] = str(row.get("expiration_date"))[:10]
        lead["x_date_line"] = row.get("line_of_business")


def list_prospects(nc: Any, supa: Any = None, *, limit: int = 200) -> dict[str, Any]:
    """Live NowCerts prospects → the Leads list. Read-only; caps at ``limit``.

    Pass ``supa`` to have each lead's x-date attached from the opportunity mirror.
    """
    leads: list[dict[str, Any]] = []
    for ins in nc.fetch_insureds(page_size=100):
        if _is_prospect(ins) and _name(ins):
            leads.append(_map_lead(ins))
            if len(leads) >= limit:
                break
    if supa is not None:
        _attach_x_dates(supa, leads)
    return {"count": len(leads), "leads": leads}
