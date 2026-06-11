"""Command Center dashboard reads — KPIs, approval queue, activity feed.

Wired to live data where it's cheap and honest:
  - KPIs from the canonical book (canonical_clients / canonical_policies) + the
    latest agency snapshot for retention.
  - Approval queue from cc_submissions still in review (Phase 1 output).
  - Feed from cc_review_events (the real intake activity).

Pipeline stays None until the Espo opportunity read is wired (Phase 4). We show
real premium/clients, not a placeholder, and compare retention to RSG's standing
75% target (the book was 54.92%) rather than an invented goal.
"""
from __future__ import annotations

from typing import Any

RETENTION_GOAL = 75.0   # RSG standing target


def _premium(p: dict) -> float:
    for k in ("annualized_premium", "current_term_amount", "premium_amount"):
        v = p.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def kpi_summary(supa) -> dict[str, Any]:
    clients = supa.select("canonical_clients", columns="nowcerts_insured_guid", limit=5000)
    policies = supa.select(
        "canonical_policies",
        columns="active,annualized_premium,current_term_amount,premium_amount",
        limit=5000,
    )
    active = [p for p in policies if p.get("active")]
    snap = supa.select("agency_snapshots", params={"order": "snapshot_date.desc"}, limit=1)
    s = snap[0] if snap else {}
    return {
        "active_premium": round(sum(_premium(p) for p in active)),
        "total_premium": round(sum(_premium(p) for p in policies)),
        "client_count": len(clients),
        "policy_count": len(policies),
        "active_policy_count": len(active),
        "retention_rate": s.get("retention_rate"),
        "retention_goal": RETENTION_GOAL,
        "retention_snapshot_date": s.get("snapshot_date"),
        "pipeline": None,   # Phase 4 — Espo opportunities
    }


def approval_queue(supa, limit: int = 50) -> list[dict]:
    rows = supa.select("cc_submissions", params={
        "status": "eq.in_review", "order": "updated_at.desc",
    }, limit=limit)
    out = []
    for r in rows:
        flags = r.get("flags") or []
        out.append({
            "id": r.get("id"),
            "client_name": r.get("client_name"),
            "lane": r.get("lane"),
            "created_by": r.get("created_by"),
            "updated_at": r.get("updated_at"),
            "blocking": sum(1 for f in flags if f.get("severity") == "blocking"),
            "warnings": sum(1 for f in flags if f.get("severity") == "warning"),
        })
    return out


def activity_feed(supa, limit: int = 25) -> list[dict]:
    return supa.select("cc_review_events", params={"order": "at.desc"}, limit=limit)
