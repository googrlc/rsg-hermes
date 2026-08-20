"""AUT-12 duplicate detection — flag for review, never auto-delete."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from hermes.desk.routing import TicketSnapshot

_WORDS = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class DuplicateFlag:
    other_ticket_id: str
    reasons: tuple[str, ...]


def _tokens(text: str | None) -> set[str]:
    return set(_WORDS.findall((text or "").lower()))


def similar_subject(left: str | None, right: str | None, *, threshold: float = 0.5) -> bool:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return False
    overlap = len(a & b)
    return (overlap / min(len(a), len(b))) >= threshold


def possible_duplicates(
    candidate: TicketSnapshot,
    open_tickets: list[tuple[str, TicketSnapshot]],
    *,
    today: date | None = None,
    lookback_days: int = 14,
) -> tuple[DuplicateFlag, ...]:
    """Return review flags. Callers must not delete matches automatically."""
    today = today or date.today()
    flags: list[DuplicateFlag] = []
    for ticket_id, other in open_tickets:
        reasons: list[str] = []
        if candidate.contact_id and candidate.contact_id == other.contact_id:
            reasons.append("same_contact")
        if candidate.policy_number and candidate.policy_number == other.policy_number:
            reasons.append("same_policy")
        if candidate.category and candidate.category == other.category:
            reasons.append("same_category")
        if similar_subject(candidate.subject, other.subject):
            reasons.append("similar_subject")
        created = other.fields.get("created_on")
        if isinstance(created, date) and created >= today - timedelta(days=lookback_days):
            reasons.append("recent")
        # Need the identity overlap plus similarity — same contact+policy+category
        # is enough; otherwise require similar subject plus one identity key.
        identity = {"same_contact", "same_policy", "same_category"}
        if identity <= set(reasons) or (
            "similar_subject" in reasons and len(set(reasons) & identity) >= 1
        ):
            flags.append(DuplicateFlag(other_ticket_id=ticket_id, reasons=tuple(reasons)))
    return tuple(flags)
