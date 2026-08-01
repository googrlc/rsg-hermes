"""Carriers — the appetite read.

A single endpoint over `carrier_appetite`: which carriers RSG can place a risk
with, filtered by carrier, state, line of business and NAICS.

Note there are TWO /api/carriers in the estate. This one is the Hermes read off
Supabase; rsg-carrierhub serves its own on :3200/:8445 with a different shape.
They are not interchangeable — check which one a caller means before changing
either. This router is the natural thing to retire into carrierhub when that
app takes the surface over (docs/repo-split-plan.md, extraction #2).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from hermes_app import deps

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/carriers")
def list_carrier_appetite(
    limit: int = 500,
    carrier: str | None = None,
    state: str | None = None,
    lob: str | None = None,
    naics: str | None = None,
):
    """Carrier appetite reference — which carriers RSG can place a risk with, by
    line of business, state, and class code (read-only). Backs the Carrier Hub.
    Filter by carrier (partial), state (2-letter), lob (partial), or naics/class
    code.

    `states_approved` and `class_codes` are text[] on the table, so state and code
    filtering happens in Python — a row scoped ["ALL"] is a nationwide appointment
    and must survive a state filter. Absence of a match here is not a declination:
    the table is a reference, not the carrier's answer.
    """
    from hermes import carriers as CA

    params: dict[str, str] = {"order": "carrier_name.asc", "active": "eq.true"}
    if carrier:
        params["carrier_name"] = f"ilike.*{carrier}*"
    if lob:
        params["lob"] = f"ilike.*{lob}*"
    rows = deps.get_supa().select(
        "carrier_appetite", columns=CA.APPETITE_COLUMNS, params=params, limit=limit,
    )
    rows = CA.filter_by_state(rows, state)
    if naics:
        rows = [r for r in rows if CA.matches_code(r, naics)]
    return {"carriers": rows, "count": len(rows)}


_RENEWAL_LOST = {"cancelled", "non-renewed", "non-renewal", "lapsed", "expired", "flat cancel", "rewritten"}
