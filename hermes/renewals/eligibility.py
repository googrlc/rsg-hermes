"""Centralized renewal-eligibility rule — the single source of truth.

A renewal candidate is ONE upcoming renewal *event*, not a policy row. This
module implements the exact eligibility rule once, as a pure function, so every
layer (NowCerts selection -> candidate build -> Supabase event -> cockpit queue
-> Hermes execution-time revalidation) asks the same question and gets the
same answer.

    insured_is_active
    AND event_date in [today, today + 120]
    AND exactly one of:
      A. CURRENT TERM  — Active/In Force/Bound, effective<=today<expiration,
         latest current term in its lineage, no valid successor supersedes it.
      B. STAGED NEXT TERM — Up for Renewal / Renewing / Renewal, same active
         insured, follows a current active term with aligned dates, not yet active.

Always excluded: Cancelled/Canceled, Expired, Flat Cancel, Non-Renewed, Lapsed;
superseded Renewed/Rewritten; historical/orphaned/ambiguous; inactive insured;
no trustworthy event date. "Unmatched" is NOT auto-excluded — it routes to
`needs_verification` for a live NowCerts check, never silently dropped.

`risk_status` is neither an input nor an output here — it only grades the urgency
of an *already-eligible* event, elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from . import cadence

# --- states / branches --------------------------------------------------------
STATE_ELIGIBLE = "eligible"
STATE_NEEDS_VERIFICATION = "needs_verification"
STATE_EXCLUDED = "excluded"

BRANCH_CURRENT_TERM = "current_term"
BRANCH_STAGED_NEXT_TERM = "staged_next_term"
BRANCH_MEDICARE_ANNUAL = "medicare_annual"

DISCOVERY_WINDOW_DAYS = 120

# --- normalized lifecycle status (docs/integrations/nowcerts-import-mapping.md §2) ---
_STATUS_NORMALIZE = {
    "active": "Active", "in force": "Active", "inforce": "Active", "bound": "Active",
    "up for renewal": "Up for Renewal", "renewal pending": "Up for Renewal",
    "renewing": "Renewing", "in renewal": "Renewing", "renewal": "Renewing",
    "renewed": "Renewed", "rewritten": "Rewritten",
    "expired": "Expired",
    "cancelled": "Cancelled", "canceled": "Cancelled",
    "flat cancel": "Flat Cancel", "flat-cancel": "Flat Cancel", "flat cancelled": "Flat Cancel",
    "pending cancel": "Pending Cancel", "pending cancellation": "Pending Cancel", "cxl pending": "Pending Cancel",
    "non-renewed": "Non-Renewed", "non renewed": "Non-Renewed", "nonrenewed": "Non-Renewed",
    "lapsed": "Lapsed",
}

CURRENT_STATUSES = frozenset({"Active"})              # active / in force / bound
STAGED_STATUSES = frozenset({"Up for Renewal", "Renewing"})
# The spec's always-exclude set (Pending Cancel is deliberately NOT here — it is
# ambiguous and routes to needs_verification via the catch-all).
EXCLUDE_STATUSES = frozenset({"Expired", "Cancelled", "Flat Cancel", "Non-Renewed", "Lapsed"})
SUPERSEDED_STATUSES = frozenset({"Renewed", "Rewritten"})

# --- workflow-entry thresholds (Site pool = 120d; entry is per segment) --------
WORKFLOW_ENTRY_DAYS = {
    cadence.cc.SEGMENT_COMMERCIAL_SMALL: 90,
    cadence.cc.SEGMENT_COMMERCIAL_MID: 90,
    cadence.cc.SEGMENT_PERSONAL_12MO: 30,
    cadence.cc.SEGMENT_AUTO_6MO: 30,
    cadence.cc.SEGMENT_BENEFITS: 120,
}
SEGMENT_MEDICARE = "medicare"
DEFAULT_WORKFLOW_ENTRY_DAYS = 30


def normalize_status(raw: Any) -> str:
    """Map any AMS status spelling to the canonical enum, or '' if unknown."""
    return _STATUS_NORMALIZE.get(str(raw or "").strip().lower(), "")


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _lob(policy: dict[str, Any]) -> str:
    for key in ("line_of_business", "lineOfBusiness", "lines_of_business", "lob"):
        val = policy.get(key)
        if val:
            return str(val)
    return ""


def _status_raw(policy: dict[str, Any]) -> Any:
    for key in ("status", "policyStatus", "PolicyStatus", "normalized_status", "policy_status"):
        if policy.get(key) is not None:
            return policy.get(key)
    return ""


def next_aug_1(today: date) -> date:
    """The next Medicare AEP anchor (Aug 1 on/after today)."""
    aug1 = date(today.year, 8, 1)
    return aug1 if aug1 >= today else date(today.year + 1, 8, 1)


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------
@dataclass
class LineageContext:
    """Predecessor/successor facts the refresh layer derives per policy."""

    lineage_id: str
    predecessor_policy_number: str | None = None
    successor_policy_number: str | None = None
    has_valid_successor: bool = False       # a staged/active successor already supersedes this current term
    follows_current_term: bool = False      # for staged: a current active term this aligns to exists


def _normalized_lob_key(lob: str) -> str:
    low = lob.strip().lower()
    if cadence.is_medicare(low):
        return "medicare"
    if cadence.is_benefits(low):
        return "benefits"
    if cadence.is_commercial(low):
        return "commercial"
    return "personal"


def derive_lineage_id(policy: dict[str, Any], *, root_policy_number: str | None = None) -> str:
    """Deterministic lineage key: insured guid + normalized LOB + root policy number.

    ``root_policy_number`` is the earliest ancestor number the refresh resolved by
    walking ``renewed_policy``; when absent, the policy's own number is the root
    (same-number renewals and brand-new business both collapse to their number).
    """
    insured = str(policy.get("nowcerts_insured_guid") or policy.get("insuredDatabaseId")
                  or policy.get("insured_id") or "").strip()
    number = str(policy.get("policy_number") or policy.get("number") or "").strip()
    root = (root_policy_number or number or "").strip()
    return f"{insured}:{_normalized_lob_key(_lob(policy))}:{root}"


def self_lineage(policy: dict[str, Any]) -> LineageContext:
    return LineageContext(lineage_id=derive_lineage_id(policy))


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class EligibilityResult:
    state: str
    reason: str
    branch: str | None = None
    event_date: date | None = None
    segment: str | None = None
    line_of_business: str = ""
    lineage_id: str = ""
    normalized_status: str = ""
    workflow_entry_date: date | None = None
    in_working_queue: bool = False
    is_medicare: bool = False
    predecessor_policy_number: str | None = None
    successor_policy_number: str | None = None

    @property
    def eligible(self) -> bool:
        return self.state == STATE_ELIGIBLE


def _workflow_entry(segment: str | None, event_date: date, *, is_medicare: bool) -> date:
    if is_medicare:
        return event_date  # AEP: enter the working queue on Aug 1 itself
    days = WORKFLOW_ENTRY_DAYS.get(segment, DEFAULT_WORKFLOW_ENTRY_DAYS)
    return event_date - timedelta(days=days)


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------
def evaluate(
    policy: dict[str, Any],
    *,
    insured_active: bool,
    today: date,
    account_active_premium: float | None = None,
    lineage: LineageContext | None = None,
    discovery_window_days: int = DISCOVERY_WINDOW_DAYS,
) -> EligibilityResult:
    """Classify one policy into eligible / needs_verification / excluded."""
    lob = _lob(policy)
    medicare = cadence.is_medicare(lob.lower())
    segment = SEGMENT_MEDICARE if medicare else cadence.classify_segment(
        policy, account_active_premium=account_active_premium
    )
    lineage = lineage or self_lineage(policy)
    status = normalize_status(_status_raw(policy))
    eff = _parse_date(policy.get("effective_date") or policy.get("effectiveDate"))
    exp = _parse_date(policy.get("expiration_date") or policy.get("expirationDate"))

    def _mk(state: str, reason: str, **kw: Any) -> EligibilityResult:
        return EligibilityResult(
            state=state, reason=reason, segment=segment, line_of_business=lob,
            lineage_id=lineage.lineage_id, normalized_status=status, is_medicare=medicare,
            predecessor_policy_number=lineage.predecessor_policy_number,
            successor_policy_number=lineage.successor_policy_number, **kw,
        )

    def _eligible(branch: str, event_date: date) -> EligibilityResult:
        entry = _workflow_entry(segment, event_date, is_medicare=medicare)
        return _mk(
            STATE_ELIGIBLE, f"eligible ({branch})", branch=branch, event_date=event_date,
            workflow_entry_date=entry, in_working_queue=today >= entry,
        )

    # 0. Inactive insured — hard exclude regardless of policy state.
    if not insured_active:
        return _mk(STATE_EXCLUDED, "insured is not active")

    # 1. Medicare — annual Aug 1 (AEP) event, not policy-term driven.
    if medicare:
        event_date = next_aug_1(today)
        if (event_date - today).days > discovery_window_days:
            return _mk(STATE_EXCLUDED, f"medicare AEP {event_date.isoformat()} outside {discovery_window_days}-day window")
        return _eligible(BRANCH_MEDICARE_ANNUAL, event_date)

    # 2. Terminal / dead lifecycle. Exclude — UNLESS dates say it is still an
    #    in-force current term (dirty data), which routes to verification.
    if status in EXCLUDE_STATUSES:
        if eff and exp and eff <= today < exp:
            return _mk(STATE_NEEDS_VERIFICATION,
                       f"status {status} but dates indicate an in-force current term — verify in NowCerts")
        return _mk(STATE_EXCLUDED, f"lifecycle status {status or 'unknown'}")
    if status in SUPERSEDED_STATUSES:
        return _mk(STATE_EXCLUDED, f"superseded term ({status})")

    # 3. Branch A — CURRENT TERM.
    if status in CURRENT_STATUSES and eff and exp and eff <= today < exp:
        if lineage.has_valid_successor:
            return _mk(STATE_EXCLUDED, "current term superseded by a staged successor (event tracked on the successor)")
        if (exp - today).days > discovery_window_days:
            return _mk(STATE_EXCLUDED, f"expiration {exp.isoformat()} outside {discovery_window_days}-day window")
        return _eligible(BRANCH_CURRENT_TERM, exp)

    # 4. Branch B — STAGED NEXT TERM (must not yet be active).
    if status in STAGED_STATUSES:
        if eff and eff > today:
            if not lineage.follows_current_term:
                return _mk(STATE_NEEDS_VERIFICATION,
                           "staged term does not clearly follow a current active term — verify lineage in NowCerts")
            if (eff - today).days > discovery_window_days:
                return _mk(STATE_EXCLUDED, f"staged effective {eff.isoformat()} outside {discovery_window_days}-day window")
            return _eligible(BRANCH_STAGED_NEXT_TERM, eff)
        if not (eff or exp):
            return _mk(STATE_EXCLUDED, "no trustworthy event date")
        return _mk(STATE_NEEDS_VERIFICATION,
                   f"staged status {status} without a future effective date — verify in NowCerts")

    # 5. Everything else (Pending Cancel, Active-but-not-current, no/expired dates,
    #    unmatched) — never silently drop; send to verification unless truly dateless.
    if not exp:
        return _mk(STATE_EXCLUDED, "no trustworthy event date")
    if exp <= today:
        return _mk(STATE_NEEDS_VERIFICATION,
                   f"expiration {exp.isoformat()} already passed but status is {status or 'unknown'} — verify in NowCerts")
    return _mk(STATE_NEEDS_VERIFICATION,
               f"status {status or 'unknown'} matches neither a current nor a staged term — verify in NowCerts")
