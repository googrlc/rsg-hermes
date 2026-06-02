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

from hermes.integrations.supabase_client import SupabaseClient
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
    """Return a risk_status from VALID_RISK_STATUSES.

    Order of authority: terminal lifecycle state (renewed/lapsed) → premium
    increase (when a renewal quote exists) → pending-cancel → active renewal in
    progress → expiration timing for in-force/unknown policies.
    """
    status = (policy_status or "").strip().lower()
    exp = _parse_date(expiration_date)
    days_until = (exp - today).days if exp is not None else None
    past_due = days_until is not None and days_until < 0

    # 1. Authoritative terminal states from the AMS/commission record.
    if status in RENEWED_STATUSES:
        return "RENEWED"
    if status in LAPSED_STATUSES:
        return "LAPSED"

    # 2. Premium increase (only meaningful once a renewal quote is recorded).
    if increase_percentage is not None:
        if increase_percentage > 15:
            return "CRITICAL"
        if increase_percentage >= 5:
            return "AT_RISK"

    # 3. Carrier flagged the policy for cancellation.
    if status in PENDING_CANCEL_STATUSES:
        return "CRITICAL"

    # 4. Actively in the renewal pipeline.
    if status in IN_RENEWAL_STATUSES:
        if past_due or (days_until is not None and days_until <= 30):
            return "CRITICAL"
        return "AT_RISK"

    # 5. In-force / unknown — classify on timing.
    if days_until is None:
        return "SAFE"
    if past_due:
        return "CRITICAL"  # past x-date with no renewal/lapse recorded → urgent review
    if days_until <= 30:
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


def refresh_renewals(
    supa: SupabaseClient,
    *,
    dry_run: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Reclassify every project_85_renewals row against crm_commissions.

    Writes back changed ``risk_status`` (+ backfills ``premium_current`` from the
    commission record when missing, and refreshes ``ai_strategy_notes``). Returns
    a summary with per-status counts and the number of rows changed.
    """
    today = today or date.today()

    renewals = supa.select(
        "project_85_renewals",
        columns="id,policy_number,client_name,expiration_date,premium_current,premium_renewal,risk_status,last_contact_date",
        limit=2000,
    )
    commissions = supa.select(
        "crm_commissions",
        columns="policy_number,policy_status,premium,expiration_date",
        limit=2000,
    )
    by_policy = _best_commission_by_policy(commissions)

    by_risk: dict[str, int] = {s: 0 for s in VALID_RISK_STATUSES}
    changed = 0
    matched = 0

    for r in renewals:
        comm = by_policy.get(r.get("policy_number"))
        if comm is not None:
            matched += 1
        policy_status = comm.get("policy_status") if comm else None
        exp = r.get("expiration_date")
        days_until = (_parse_date(exp) - today).days if _parse_date(exp) else None

        new_status = classify_risk(
            policy_status=policy_status,
            expiration_date=exp,
            today=today,
            increase_percentage=_as_float(r.get("increase_percentage")),
        )
        by_risk[new_status] += 1

        update: dict[str, Any] = {}
        if new_status != r.get("risk_status"):
            update["risk_status"] = new_status
        # backfill premium_current from the commission record when missing
        if not _as_float(r.get("premium_current")) and comm and _as_float(comm.get("premium")):
            update["premium_current"] = _as_float(comm.get("premium"))
        if update:
            update["ai_strategy_notes"] = build_strategy_note(new_status, policy_status, days_until)
            changed += 1
            if not dry_run:
                supa.update("project_85_renewals", r["id"], update)

    summary = {
        "dry_run": dry_run,
        "as_of": today.isoformat(),
        "total": len(renewals),
        "matched_commissions": matched,
        "changed": changed,
        "by_risk": by_risk,
    }
    log.info(
        "renewal classify: total=%d matched=%d changed=%d dry_run=%s by_risk=%s",
        len(renewals), matched, changed, dry_run, by_risk,
    )
    return summary
