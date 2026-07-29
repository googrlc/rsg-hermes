"""Project 85 renewal operations — Supabase-backed renewal tracking."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)

# risk_status describes the URGENCY of an already-eligible renewal event — it must
# never decide whether a policy is a renewal (eligibility lives in
# renewal_candidates.eligibility_state). The classifier now emits only these three.
URGENCY_RISK_STATUSES = ("SAFE", "AT_RISK", "CRITICAL")
# The full tuple stays for DB-enum / legacy-row validation compatibility; RENEWED
# and LAPSED are terminal lifecycle outcomes, no longer produced as risk values.
VALID_RISK_STATUSES = ("SAFE", "AT_RISK", "CRITICAL", "RENEWED", "LAPSED")
VALID_ACTION_TYPES = (
    "EMAIL_SENT",
    "SLACK_ALERT",
    "QUOTE_GENERATED",
    "PHONE_CALL",
    "MEETING_SCHEDULED",
    "PROPOSAL_SENT",
    "BOUND",
    "MANUAL_NOTE",
    "RISK_ESCALATION",
    # Renewal Executor (Job Contract v2) outcome types — one per executed job.
    "REQUEST_TERMS",
    "PREPARE_OPTIONS",
    "CLIENT_FOLLOW_UP",
    "AMS_UPDATE",
    "EXECUTION_BLOCKED",
    "EXECUTION_FAILED",
)


def upsert_renewal(
    supa: SupabaseClient,
    *,
    policy_number: str,
    client_name: str,
    expiration_date: str,
    premium_current: float | None = None,
    premium_renewal: float | None = None,
    risk_status: str = "SAFE",
    ai_strategy_notes: str | None = None,
    last_contact_date: str | None = None,
) -> dict[str, Any]:
    """Insert or update a Project 85 renewal record."""
    if risk_status not in VALID_RISK_STATUSES:
        raise ValueError(f"Invalid risk_status: {risk_status}; must be one of {VALID_RISK_STATUSES}")

    payload: dict[str, Any] = {
        "policy_number": policy_number,
        "client_name": client_name,
        "expiration_date": expiration_date,
        "risk_status": risk_status,
    }
    if premium_current is not None:
        payload["premium_current"] = premium_current
    if premium_renewal is not None:
        payload["premium_renewal"] = premium_renewal
    if ai_strategy_notes is not None:
        payload["ai_strategy_notes"] = ai_strategy_notes
    if last_contact_date is not None:
        payload["last_contact_date"] = last_contact_date

    return supa.upsert("project_85_renewals", payload, on_conflict="policy_number")


def log_renewal_action(
    supa: SupabaseClient,
    *,
    renewal_id: str,
    action_type: str,
    details: dict[str, Any] | None = None,
    performed_by_role: str = "HermesRenewalSpecialist",
) -> dict[str, Any]:
    """Append an action record to ``renewal_actions``."""
    if action_type not in VALID_ACTION_TYPES:
        raise ValueError(f"Invalid action_type: {action_type}; must be one of {VALID_ACTION_TYPES}")
    return supa.insert(
        "renewal_actions",
        {
            "renewal_id": renewal_id,
            "action_type": action_type,
            "details": details or {},
            "performed_by_role": performed_by_role,
        },
    )


def get_renewals_by_risk(
    supa: SupabaseClient,
    risk_status: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch renewals filtered by risk status."""
    return supa.select(
        "project_85_renewals",
        params={"risk_status": f"eq.{risk_status}"},
        limit=limit,
    )


def get_renewals_expiring_within(
    supa: SupabaseClient,
    days: int,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch renewals expiring within the next N days."""
    from datetime import timedelta
    target = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    return supa.select(
        "project_85_renewals",
        params={
            "and": f"(expiration_date.gte.{target},expiration_date.lte.{cutoff})",
            "order": "expiration_date.asc",
        },
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Command Center — Renewals Cockpit (Phase 1)
# ---------------------------------------------------------------------------

# Buckets are ordered most-urgent-first; ``upcoming`` covers the next 90 days.
RENEWAL_BUCKETS = ("past_due", "le7", "le30", "le60", "le90", "gt90", "no_date")

# What the book knows about a renewal's policy that project_85_renewals does not.
# The renewal table holds the desk's own working fields plus an expiration date; the
# policy itself lives in canonical_policies.
_POLICY_DATE_COLUMNS = "policy_number,effective_date,expiration_date,carrier,lines_of_business"
_POLICY_FIELDS = ("effective_date", "carrier", "lines_of_business")


def _quoted_in_list(values: list[str]) -> str:
    """A PostgREST ``in.(…)`` list, each value quoted.

    Policy numbers are free text and routinely carry commas, spaces and parens —
    unquoted they would split the filter into nonsense terms.
    """
    quoted = ['"' + v.replace('\\', '\\\\').replace('"', '\\"') + '"' for v in values]
    return f"in.({','.join(quoted)})"


def attach_policy_dates(
    supa: SupabaseClient,
    rows: list[dict[str, Any]],
    *,
    batch_size: int = 200,
) -> list[dict[str, Any]]:
    """Hang each renewal's policy dates on it, in place, from ``canonical_policies``.

    A renewal row carries the expiration date and nothing else about the term, so
    the desk can see when cover runs out but not when it started — and "renews in
    30 days" reads very differently on a policy written last month than on one that
    has been in force for a year. The carrier and line come along for the same
    reason: the renewal table stores neither, so the list has been showing a policy
    number under a column headed Line.

    Degrades to the rows as they were if the book read fails; missing policy dates
    are worth less than an empty renewals cockpit.
    """
    by_number: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        number = str(row.get("policy_number") or "").strip()
        if number:
            by_number.setdefault(number, []).append(row)
    if not by_number:
        return rows

    numbers = list(by_number)
    for start in range(0, len(numbers), batch_size):
        batch = numbers[start:start + batch_size]
        try:
            policies = supa.select(
                "canonical_policies",
                columns=_POLICY_DATE_COLUMNS,
                params={"policy_number": _quoted_in_list(batch), "order": "effective_date.asc"},
                limit=batch_size * 5,
            )
        except Exception:  # noqa: BLE001 — see the docstring: degrade, never 500
            log.exception("renewals: policy date lookup failed for %d policies", len(batch))
            continue
        for policy in policies:
            for row in by_number.get(str(policy.get("policy_number") or "").strip(), []):
                # A policy number can carry several terms. Ordered by effective date
                # ascending, the LAST one whose expiration matches the renewal is the
                # term being renewed; fall back to the latest term when none matches,
                # which is what a renewal with a corrected expiration date looks like.
                exact = str(policy.get("expiration_date") or "")[:10] == str(row.get("expiration_date") or "")[:10]
                if row.get("_policy_exact") and not exact:
                    continue
                for field in _POLICY_FIELDS:
                    if policy.get(field) is not None:
                        row[field] = policy.get(field)
                row["_policy_exact"] = exact
    for row in rows:
        row.pop("_policy_exact", None)
    return rows


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _iso_or_none(value: Any) -> str | None:
    parsed = _parse_iso_date(value)
    return parsed.isoformat() if parsed else None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bucket_for(days_until: int | None) -> str:
    if days_until is None:
        return "no_date"
    if days_until < 0:
        return "past_due"
    if days_until <= 7:
        return "le7"
    if days_until <= 30:
        return "le30"
    if days_until <= 60:
        return "le60"
    if days_until <= 90:
        return "le90"
    return "gt90"


def summarize_renewals(
    rows: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Pure aggregation for the Command Center Renewals Cockpit.

    Given raw ``project_85_renewals`` rows, returns urgency buckets (with counts
    and summed current premium) plus a list of renewals expiring in the next 90
    days, sorted by urgency then premium. No network — safe to unit-test.
    """
    today = today or date.today()
    buckets = {b: {"count": 0, "premium_current": 0.0} for b in RENEWAL_BUCKETS}
    by_risk: dict[str, dict[str, Any]] = {
        s: {"count": 0, "premium_current": 0.0} for s in VALID_RISK_STATUSES
    }
    upcoming: list[dict[str, Any]] = []

    for row in rows:
        exp = _parse_iso_date(row.get("expiration_date"))
        days_until = (exp - today).days if exp is not None else None
        bucket = _bucket_for(days_until)
        premium = _as_float(row.get("premium_current")) or 0.0
        buckets[bucket]["count"] += 1
        buckets[bucket]["premium_current"] += premium

        risk = row.get("risk_status")
        if risk in by_risk:
            by_risk[risk]["count"] += 1
            by_risk[risk]["premium_current"] += premium

        if days_until is not None and 0 <= days_until <= 90:
            upcoming.append(
                {
                    "id": row.get("id"),
                    "client_name": row.get("client_name"),
                    "policy_number": row.get("policy_number"),
                    "expiration_date": exp.isoformat() if exp else None,
                    # Present when the row has been through attach_policy_dates;
                    # None rather than absent so the cockpit renders one shape.
                    "effective_date": _iso_or_none(row.get("effective_date")),
                    "carrier": row.get("carrier"),
                    "lines_of_business": row.get("lines_of_business"),
                    "days_until": days_until,
                    "premium_current": _as_float(row.get("premium_current")),
                    "premium_renewal": _as_float(row.get("premium_renewal")),
                    "increase_percentage": _as_float(row.get("increase_percentage")),
                    "risk_status": row.get("risk_status"),
                    "ai_strategy_notes": row.get("ai_strategy_notes"),
                    "last_contact_date": row.get("last_contact_date"),
                    # What the source said before a human corrected it, carried
                    # through so the desk sees a corrected number as a decision
                    # rather than as the AMS's word.
                    "_overridden": row.get("_overridden"),
                }
            )

    upcoming.sort(key=lambda r: (r["days_until"], -(r["premium_current"] or 0.0)))
    for stats in buckets.values():
        stats["premium_current"] = round(stats["premium_current"], 2)
    for stats in by_risk.values():
        stats["premium_current"] = round(stats["premium_current"], 2)

    return {
        "as_of": today.isoformat(),
        "buckets": buckets,
        "by_risk": by_risk,
        "upcoming": upcoming,
        "upcoming_count": len(upcoming),
        "past_due_count": buckets["past_due"]["count"],
        "total": len(rows),
    }


def escalate_risk(
    supa: SupabaseClient,
    *,
    renewal_id: str,
    new_status: str,
    reason: str,
    performed_by_role: str = "HermesRenewalSpecialist",
) -> dict[str, Any]:
    """Change a renewal's risk status and log the escalation."""
    if new_status not in VALID_RISK_STATUSES:
        raise ValueError(f"Invalid risk_status: {new_status}; must be one of {VALID_RISK_STATUSES}")

    supa.update(
        "project_85_renewals",
        renewal_id,
        {"risk_status": new_status},
    )

    return log_renewal_action(
        supa,
        renewal_id=renewal_id,
        action_type="RISK_ESCALATION",
        details={"new_status": new_status, "reason": reason},
        performed_by_role=performed_by_role,
    )
