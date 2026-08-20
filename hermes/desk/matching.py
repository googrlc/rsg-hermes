"""CF-01 account and policy matching. Multiple hits flag for manual selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class AccountMatch:
    desk_contact_id: str | None = None
    desk_account_id: str | None = None
    ams_client_id: str | None = None
    policy_number: str | None = None
    producer: str | None = None
    service_owner: str | None = None
    score: int = 0


@dataclass(frozen=True)
class MatchResult:
    status: str  # matched | unmatched | manual_selection
    match: AccountMatch | None = None
    candidates: tuple[AccountMatch, ...] = ()


def resolve_account(candidates: Sequence[AccountMatch]) -> MatchResult:
    ranked = tuple(sorted(candidates, key=lambda row: row.score, reverse=True))
    if not ranked:
        return MatchResult(status="unmatched")
    top = ranked[0]
    ties = tuple(row for row in ranked if row.score == top.score)
    if len(ties) == 1:
        return MatchResult(status="matched", match=top, candidates=ranked)
    return MatchResult(status="manual_selection", candidates=ranked)
