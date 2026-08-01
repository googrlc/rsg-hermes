"""Renewal risk classification/refresh (Command Center, Phase 1.5).

Turns the ``project_85_renewals`` watchlist from an all-SAFE pile into a real
risk view by keying off the authoritative policy lifecycle state carried on
``crm_commissions.policy_status`` (Active / Expired / Cancelled / Renewed /
Up for Renewal / …), falling back to expiration timing when no commission row
matches. The classifier is pure and unit-tested; ``refresh_renewals`` is the
driver that reads both tables and writes back changed rows.

Note: ``project_85_renewals.increase_percentage`` is a GENERATED column — never
write it. Only ``premium_current`` / ``premium_renewal`` feed it.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from hermes_integrations.supabase_client import SupabaseClient
from hermes.operations.renewal_tracker import VALID_RISK_STATUSES

log = logging.getLogger(__name__)

# crm_commissions.policy_status vocab → lifecycle buckets (compared lowercased).
RENEWED_STATUSES = {"renewed"}
LAPSED_STATUSES = {
    "expired",
    "cancelled",
    "canceled",
    "flat cancel",
    "non-renewed",
    "non renewed",
}
IN_RENEWAL_STATUSES = {"up for renewal", "renewing"}
PENDING_CANCEL_STATUSES = {"pending cancel"}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def classify_risk(
    *,
    policy_status: str | None,
    expiration_date: Any,
    today: date,
    increase_percentage: float | None = None,
) -> str:
    """URGENCY of an ALREADY-ELIGIBLE renewal event: SAFE | AT_RISK | CRITICAL.

    Grades urgency only — it must NEVER decide whether a policy is a renewal.
    Eligibility is owned entirely by ``hermes.renewals.eligibility``; terminal
    lifecycle states (renewed/lapsed/cancelled) are excluded upstream and never
    reach here, so this no longer emits RENEWED/LAPSED.

    Order: premium increase (when a renewal quote exists) → expiration timing.
    """
    exp = _parse_date(expiration_date)
    days_until = (exp - today).days if exp is not None else None
    past_due = days_until is not None and days_until < 0

    if increase_percentage is not None:
        if increase_percentage > 15:
            return "CRITICAL"
        if increase_percentage >= 5:
            return "AT_RISK"

    if days_until is None:
        return "SAFE"
    if past_due or days_until <= 30:
        return "CRITICAL"
    if days_until <= 90:
        return "AT_RISK"
    return "SAFE"


def build_strategy_note(risk_status: str, policy_status: str | None, days_until: int | None) -> str:
    """Short, human-readable guidance for the ai_strategy_notes column."""
    src = f" (AMS: {policy_status})" if policy_status else ""
    when = (
        "no expiration on file"
        if days_until is None
        else f"{abs(days_until)} days {'past x-date' if days_until < 0 else 'to x-date'}"
    )
    blurb = {
        "RENEWED": "Renewed — confirm bind details and close out.",
        "LAPSED": "Lapsed/expired — win-back candidate; confirm whether truly lost.",
        "CRITICAL": "Critical — contact now; renewal at/over x-date or premium spike.",
        "AT_RISK": "At risk — schedule renewal outreach and remarket if needed.",
        "SAFE": "On track — monitor at the next checkpoint.",
    }.get(risk_status, "Review.")
    return f"{blurb} {when}{src}. [auto-classified]"


def _best_commission_by_policy(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pick one commission row per policy_number (latest expiration wins)."""
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        pn = row.get("policy_number")
        if not pn:
            continue
        cur = best.get(pn)
        if cur is None:
            best[pn] = row
            continue
        if (_parse_date(row.get("expiration_date")) or date.min) >= (
            _parse_date(cur.get("expiration_date")) or date.min
        ):
            best[pn] = row
    return best


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _increase_pct(row: dict[str, Any]) -> float | None:
    cur = _as_float(row.get("premium_current"))
    ren = _as_float(row.get("premium_renewal"))
    if cur and cur > 0 and ren is not None:
        return (ren - cur) / cur * 100
    return None


def refresh_renewals(
    supa: SupabaseClient,
    *,
    dry_run: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Re-grade URGENCY (risk_status) over ELIGIBLE renewal_candidates.

    Urgency-only: this never changes ``eligibility_state`` — the eligibility
    engine owns membership. It updates ``renewal_candidates.risk_status`` and
    mirrors it onto the project_85_renewals projection by policy_number. A full
    rebuild (candidate_refresh.run_refresh / ``--renewal-refresh``) is what
    (re)computes eligibility itself.
    """
    today = today or date.today()

    rows = supa.select(
        "renewal_candidates",
        params={"eligibility_state": "eq.eligible"},
        columns="id,policy_number,renewal_event_date,normalized_status,premium_current,premium_renewal,risk_status",
        limit=5000,
    )

    by_risk: dict[str, int] = {s: 0 for s in VALID_RISK_STATUSES}
    changed = 0
    for r in rows:
        new_status = classify_risk(
            policy_status=r.get("normalized_status"),
            expiration_date=r.get("renewal_event_date"),
            today=today,
            increase_percentage=_increase_pct(r),
        )
        by_risk[new_status] = by_risk.get(new_status, 0) + 1
        if new_status != r.get("risk_status"):
            changed += 1
            if not dry_run:
                supa.update("renewal_candidates", r["id"], {"risk_status": new_status})
                if r.get("policy_number"):
                    supa.update_where(
                        "project_85_renewals",
                        {"risk_status": new_status},
                        filters={"policy_number": f"eq.{r['policy_number']}"},
                    )

    summary = {
        "dry_run": dry_run,
        "as_of": today.isoformat(),
        "total": len(rows),
        "changed": changed,
        "by_risk": by_risk,
    }
    log.info(
        "renewal urgency re-grade: total=%d changed=%d dry_run=%s by_risk=%s",
        len(rows), changed, dry_run, by_risk,
    )
    return summary
