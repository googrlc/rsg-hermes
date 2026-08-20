"""Priority model — keywords such as ASAP never auto-escalate."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from hermes.desk.spec import PRIORITIES

URGENT = "Urgent"
HIGH = "High"
NORMAL = "Normal"
LOW = "Low"

# Use Urgent only when one of these recorded reasons applies.
URGENT_REASONS = frozenset(
    {
        "Cancellation or lapse imminent",
        "Proof of insurance blocking active work",
        "Vehicle or driver needs coverage before operation",
        "Active claim has an immediate service problem",
        "Binding or effective-date issue",
        "Regulatory or contractual deadline imminent",
    }
)

HIGH_REASONS = frozenset(
    {
        "Time-sensitive certificate",
        "Reinstatement request",
        "Material policy correction",
        "Carrier follow-up affecting coverage",
        "Client escalation",
    }
)

# Subject/body noise that must not, by itself, set Urgent.
NOISE_KEYWORDS = ("asap", "urgent", "immediately", "right away", "as soon as possible")


def keyword_alone_is_not_urgent(text: str | None) -> bool:
    """True when the text contains urgency language but that is not enough."""
    blob = (text or "").lower()
    return any(token in blob for token in NOISE_KEYWORDS)


def recommend_priority(
    *,
    urgency_reason: str | None = None,
    required_by: date | None = None,
    cancellation_warning: bool = False,
    cancellation_date: date | None = None,
    today: date | None = None,
    business_impact: str | None = None,
) -> str:
    """Return a Desk priority. Urgent requires a recorded urgency reason."""
    today = today or date.today()
    reason = (urgency_reason or "").strip()

    if reason in URGENT_REASONS:
        return URGENT

    if cancellation_warning:
        if cancellation_date is not None and cancellation_date <= today + timedelta(days=3):
            return URGENT if reason in URGENT_REASONS or reason == "Cancellation or lapse imminent" else HIGH
        return HIGH

    if reason in HIGH_REASONS:
        return HIGH

    if required_by is not None:
        remaining = (required_by - today).days
        if remaining <= 1:
            return HIGH
        if remaining <= 3 and business_impact in {"Coverage at risk", "Work blocked"}:
            return HIGH

    if business_impact == "Informational" and not reason:
        return LOW

    return NORMAL


def assert_known_priority(value: str) -> str:
    if value not in PRIORITIES:
        raise ValueError(f"Unknown priority {value!r}; expected one of {PRIORITIES}")
    return value


def escalate_for_required_by(
    current: str,
    *,
    required_by: date,
    today: date | None = None,
    thresholds: Iterable[int] = (3, 1, 0),
) -> str:
    """AUT-09: raise priority as the required-by date approaches."""
    today = today or date.today()
    remaining = (required_by - today).days
    order = list(PRIORITIES)
    idx = order.index(current) if current in order else order.index(NORMAL)
    # PRIORITIES is Urgent, High, Normal, Low — lower index is hotter.
    if remaining <= min(thresholds):
        return URGENT if current == HIGH else HIGH if idx >= 2 else current
    if remaining <= max(thresholds):
        return HIGH if idx >= 2 else current
    return current
