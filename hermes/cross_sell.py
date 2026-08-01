"""Cross-sell — search active clients to pull one into the pipeline.

Active insureds are NOT auto-fed anywhere; you search, pull up a client, and add a
cross-sell opportunity (POST /api/opportunities with opportunity_type='Cross-selling').
Reads the already-synced canonical mirror (canonical_clients / canonical_policies)
— no live AMS call, no opportunity rows created here.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from hermes_core import book as ams_book


def _num(v: Any) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _chunks(seq: list[str], n: int = 50):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def search_cross_sell(supa: Any, *, query: str, limit: int = 25) -> dict[str, Any]:
    """Search active clients by name; return each with their current LOBs + premium
    so a cross-sell opportunity can be opened on a gap."""
    q = (query or "").strip()
    if not q:
        return {"query": q, "count": 0, "clients": []}

    clients = supa.select(
        "canonical_clients",
        columns="nowcerts_insured_guid,insured_name,client_type,email,phone",
        params={"insured_name": f"ilike.*{q}*", "order": "insured_name.asc"},
        limit=limit,
    )
    guids = [c.get("nowcerts_insured_guid") for c in clients if c.get("nowcerts_insured_guid")]

    active_by_guid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for batch in _chunks([g for g in guids if g]):
        rows = ams_book.select_policies(
            supa,
            columns="nowcerts_insured_guid,lines_of_business,active,annualized_premium,premium_amount",
            params={"nowcerts_insured_guid": f"in.({','.join(batch)})"},
            limit=5000,
        )
        for p in rows:
            if p.get("active"):
                active_by_guid[p.get("nowcerts_insured_guid")].append(p)

    out = []
    for c in clients:
        g = c.get("nowcerts_insured_guid")
        pols = active_by_guid.get(g, [])
        lobs = sorted({str(p.get("lines_of_business")) for p in pols if p.get("lines_of_business")})
        premium = sum(_num(p.get("annualized_premium")) or _num(p.get("premium_amount")) for p in pols)
        out.append({
            "insured_id": g,
            "client_name": c.get("insured_name"),
            "client_type": c.get("client_type"),
            "email": c.get("email"),
            "phone": c.get("phone"),
            "current_lobs": lobs,
            "active_policy_count": len(pols),
            "active_premium": round(premium),
        })
    return {"query": q, "count": len(out), "clients": out}
