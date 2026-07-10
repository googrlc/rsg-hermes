"""Renewal cadence engine — deterministic segment classifier + touch scheduler.

Two pure jobs, no LLM, no network (BRIEF — Renewal Cadence Engine v2):

1. ``classify_segment`` — put a policy in exactly one cadence segment, or return
   ``None`` for "excluded, never touch" (Medicare). Rules are evaluated in the
   brief's order; the small/mid commercial split keys off **account-level** total
   active premium so a multi-policy account gets the richer cadence for all of
   its policies.
2. ``due_touches`` — given a segment, an expiration date, today, and the record's
   touch-tracking fields, return the touches that should fire right now.
   Idempotent: a touch fires only if its field is empty AND today has reached the
   threshold AND we are still inside the backfill grace window. A 90-day heads-up
   that is 40 days late is skipped, not sent.

The config (cutoffs, term thresholds, per-segment schedule, LOB vocab) lives in
``cadence_config`` and is versioned there. This module holds only logic.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from . import cadence_config as cc

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- drift guard
def _assert_spec_matches_cadence() -> None:
    """TOUCH_SPEC day thresholds must equal the tunable CADENCE day list.

    CADENCE is what a human edits to retime a touch; TOUCH_SPEC carries the field
    slot + template for each. If someone bumps a day in one place but not the
    other, fail loudly at import rather than schedule a phantom touch.
    """
    for seg in cc.SEGMENTS:
        cadence_days = sorted(cc.CADENCE[seg]["touches"])
        spec_days = sorted(t["days"] for t in cc.TOUCH_SPEC[seg])
        if cadence_days != spec_days:
            raise AssertionError(
                f"cadence_config drift for {seg!r}: CADENCE={cadence_days} "
                f"!= TOUCH_SPEC={spec_days}"
            )


_assert_spec_matches_cadence()


# ---------------------------------------------------------------- helpers
def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _lob(policy: dict[str, Any]) -> str:
    """Lowercased line-of-business string, tolerant of naming variants."""
    for key in ("line_of_business", "lineOfBusiness", "lob"):
        val = policy.get(key)
        if val:
            return str(val).strip().lower()
    return ""


def _has(lob: str, keywords) -> bool:
    return any(k in lob for k in keywords)


# ---------------------------------------------------------------- LOB predicates
def is_medicare(lob: str) -> bool:
    return _has(lob, cc.MEDICARE_LOB_KEYWORDS)


def is_benefits(lob: str) -> bool:
    return _has(lob, cc.BENEFITS_LOB_KEYWORDS)


def is_commercial(lob: str) -> bool:
    return cc.COMMERCIAL_MARKER in lob


def is_personal(lob: str) -> bool:
    """Personal lines — a personal marker present and not a commercial LOB."""
    return not is_commercial(lob) and _has(lob, cc.PERSONAL_LOB_KEYWORDS)


def is_personal_auto(lob: str) -> bool:
    return not is_commercial(lob) and _has(lob, cc.AUTO_LOB_KEYWORDS)


# ---------------------------------------------------------------- term length
def term_days(policy: dict[str, Any]) -> int | None:
    """Policy term length in days (expiration - effective), or None if unknown."""
    eff = _parse_date(policy.get("effective_date") or policy.get("effectiveDate"))
    exp = _parse_date(policy.get("expiration_date") or policy.get("expirationDate"))
    if eff is None or exp is None:
        return None
    return (exp - eff).days


def is_six_month_term(policy: dict[str, Any]) -> bool:
    days = term_days(policy)
    return days is not None and days < cc.TERM_6MO_MAX_DAYS


# ---------------------------------------------------------------- classifier
def classify_segment(
    policy: dict[str, Any],
    *,
    account_active_premium: float | None,
) -> str | None:
    """Return the cadence segment for a policy, or None to exclude it.

    ``account_active_premium`` is the sum of all active policies for the client
    (canonical_policies joined to canonical_clients). It drives the small/mid
    commercial split so a multi-policy account is scored as a relationship, not
    per policy — four $3K policies is a $12K account and earns the mid cadence.

    Rules, evaluated in order (BRIEF §Classifier rules):
      1. Medicare LOB           -> None (excluded entirely)
      2. Group benefits LOB     -> benefits (regardless of premium)
      3. Personal auto, 6-mo    -> auto_6mo
      4. Personal lines, 12-mo  -> personal_12mo
      5. Commercial             -> small/mid by account-level premium
    """
    lob = _lob(policy)

    # 1. Medicare — skip. No touches, no cards, ever.
    if is_medicare(lob):
        return None

    # 2. Group benefits — win regardless of premium size.
    if is_benefits(lob):
        return cc.SEGMENT_BENEFITS

    # 3. 6-month-term personal auto.
    if is_personal_auto(lob) and is_six_month_term(policy):
        return cc.SEGMENT_AUTO_6MO

    # 4. Personal lines (12-month, or any non-6mo personal auto).
    if is_personal(lob):
        return cc.SEGMENT_PERSONAL_12MO

    # 5. Commercial — split on account-level total active premium.
    premium = account_active_premium or 0.0
    if premium <= cc.SMALL_COMMERCIAL_MAX_ACCOUNT_PREMIUM:
        return cc.SEGMENT_COMMERCIAL_SMALL
    return cc.SEGMENT_COMMERCIAL_MID


# ---------------------------------------------------------------- auto rotation
def auto_6mo_cycle(last_full_review: Any, today: date | None = None) -> str:
    """Decide whether this 6-mo auto cycle is a full review or a light confirm.

    One full market review per policy per ~12 months. If no full-review touch was
    logged within FULL_REVIEW_WINDOW_DAYS, this cycle is the full review;
    otherwise the off-cycle term gets a light confirmation instead.
    """
    today = today or date.today()
    last = _parse_date(last_full_review)
    if last is None:
        return "full_review"
    if (today - last).days >= cc.FULL_REVIEW_WINDOW_DAYS:
        return "full_review"
    return "light_confirm"


# ---------------------------------------------------------------- scheduler
def days_until(expiration_date: Any, today: date | None = None) -> int | None:
    today = today or date.today()
    exp = _parse_date(expiration_date)
    return (exp - today).days if exp is not None else None


def _resolve_template(spec: dict[str, Any], auto_cycle: str | None) -> str:
    """Templates are fixed strings except auto_6mo, which picks per cycle."""
    template = spec["template"]
    if isinstance(template, dict):
        # auto_6mo: choose full_review vs light_confirm; default to light.
        return template.get(auto_cycle or "light_confirm", cc.TPL_LIGHT_CONFIRM)
    return template


def due_touches(
    segment: str | None,
    *,
    expiration_date: Any,
    sent_fields: dict[str, Any] | None = None,
    today: date | None = None,
    grace_days: int | None = None,
    auto_cycle: str | None = None,
) -> list[dict[str, Any]]:
    """Return the touches due to fire for this renewal right now.

    A touch at threshold T (days before expiration) fires only when ALL hold:
      - its EspoCRM field slot is empty (not already queued/sent) — idempotency,
      - today has reached the threshold: days_until <= T,
      - it is not stale: days_until >= T - grace  (BACKFILL_GRACE_DAYS).

    So a 90-day touch fires while 80 <= days_until <= 90 (grace 10), and is
    skipped once the policy is inside 80 days — jump to the next touch instead of
    sending a heads-up for a review that is already underway.

    Excluded (segment is None) or unschedulable (no expiration) -> empty list.
    Each returned dict carries: days, field, label, template, days_until.
    """
    if segment is None:
        return []
    spec = cc.TOUCH_SPEC.get(segment)
    if not spec:
        return []
    remaining = days_until(expiration_date, today)
    if remaining is None:
        return []

    grace = cc.BACKFILL_GRACE_DAYS if grace_days is None else grace_days
    sent = sent_fields or {}
    due: list[dict[str, Any]] = []
    for touch in spec:
        threshold = touch["days"]
        field = touch["field"]
        if sent.get(field):
            continue  # already queued or sent — never fire twice
        if remaining > threshold:
            continue  # not yet at the threshold
        if remaining < threshold - grace:
            continue  # too late — skip this touch, don't send it stale
        due.append({
            "days": threshold,
            "field": field,
            "label": touch["label"],
            "template": _resolve_template(touch, auto_cycle),
            "days_until": remaining,
        })
    return due
