"""Carrier appetite — which carriers RSG can place a risk with.

Served at `/api/carrier-appetite`, with `/api/carriers` kept as a deprecated
alias. It queries the `carrier_appetite` table by carrier, state, line of
business and NAICS, and it applies the rule that a row scoped `["ALL"]` is a
nationwide appointment which must survive a state filter.

This does NOT belong in rsg-carrierhub, despite the name, and the earlier plan
to retire it there was wrong. carrierhub's `/api/carriers` returns the carrier
DIRECTORY — the `carriers` table with nested contacts and appetite, unfiltered,
for its own React app to map client-side. Different table, different shape, no
filtering, different consumer. Repointing this endpoint's caller at carrierhub
would silently drop every filter and the nationwide rule.

Its consumer is the `carrier_appetite` MCP tool (deploy/mcp-bridge/app.py), and
the same matching logic backs the agent's "who writes this?" answer via
hermes/carriers.py — both of which live on this side. So it stays here, and the
collision ends by naming rather than by moving.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from hermes_app import deps

log = logging.getLogger(__name__)

router = APIRouter()


# Canonical path. This endpoint answers "which carriers write this risk?" — it
# queries carrier_appetite with filters. carrierhub's /api/carriers is a
# different thing: the carrier DIRECTORY (the carriers table with nested
# contacts), unfiltered, feeding its own React app. Two endpoints, two shapes,
# two consumers — they only ever collided on the path.
#
# /api/carriers is kept below as a deprecated alias so the MCP bridge keeps
# working across a deploy where only one of the two containers has restarted.
@router.get("/api/carrier-appetite")
@router.get("/api/carriers", deprecated=True)
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
