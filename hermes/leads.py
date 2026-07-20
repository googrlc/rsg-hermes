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
        "state": ins.get("state"),
    }


def list_prospects(nc: Any, *, limit: int = 200) -> dict[str, Any]:
    """Live NowCerts prospects → the Leads list. Read-only; caps at ``limit``."""
    leads: list[dict[str, Any]] = []
    for ins in nc.fetch_insureds(page_size=100):
        if _is_prospect(ins) and _name(ins):
            leads.append(_map_lead(ins))
            if len(leads) >= limit:
                break
    return {"count": len(leads), "leads": leads}
