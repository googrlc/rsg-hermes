"""Commission surface reads — coverage and status rollups for the cockpit.

Why this exists: the Commissions view used to render an empty table whenever
nothing carried ``reconciliation_status='reconciled'`` — which was every row, for
the whole life of the ledger, because nothing ever wrote that status. A blank
grid reads as "no data exists". It wasn't true, and nobody noticed for months.

So a read of the commission surface always answers three questions, not one:

  1. What matches the filter?           -> the rows
  2. What else is on the surface?       -> ``status_counts``
  3. What is deliberately NOT on it?    -> ``coverage``

(3) is the one that matters. RSG seeds commission only for business effective on
or after ``HERMES_COMMISSION_SINCE`` (default 2026-01-01), which currently leaves
37 active policies (~$287K of premium) off the ledger **by choice**. A silent
exclusion is indistinguishable from a broken pipe — this codebase has already
lost a book's worth of trust to exactly that (see the canonical_policies
tombstone incident). So the exclusion is reported on every read.

Everything here is pure except ``commission_overview``, which does the I/O.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

LEDGER_TABLE = "commission_ledger"
ENTITY_TYPE = "commission_ledger"      # portal_overrides.entity_type
DEFAULT_SINCE = "2026-01-01"

# Fields a human may correct in the portal. Deliberately narrow: identity and
# reconciliation state are not hand-editable — the first is the AMS's to own,
# the second is derived from statement transactions.
OVERRIDABLE_FIELDS = frozenset({
    "gross_premium", "expected_commission", "carrier_name", "lob",
    "client_name", "policy_effective_date", "policy_expiration_date", "notes",
})

# Statuses a policy can carry and still be commissionable, mirroring
# commission_sync: only won, in-force business ledgers a commission.
COMMISSIONABLE_STATUSES = frozenset({"active", "renewed"})

# Single home: hermes.ams.book owns the tombstone vocabulary. Two independent
# copies of this string existed, so a consumer that forgot the check counted
# phantom rows as real book.
from hermes.ams.book import TOMBSTONE_PREFIX  # noqa: F401  (re-exported)

_PREMIUM_FIELDS = ("annualized_premium", "current_term_amount", "premium_amount")


def commission_since() -> str:
    """The seeding floor. Same env var commission_sync reads, so the surface and
    the seeder can never disagree about what is in scope."""
    return (os.environ.get("HERMES_COMMISSION_SINCE") or DEFAULT_SINCE).strip()[:10]


def _premium(policy: dict[str, Any]) -> float:
    for key in _PREMIUM_FIELDS:
        value = policy.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _is_tombstoned(policy: dict[str, Any]) -> bool:
    from hermes.ams.book import is_tombstoned as _shared

    return _shared(policy)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """reconciliation_status -> count, over whatever rows are handed in."""
    counts = Counter(
        str(r.get("reconciliation_status") or "unknown").strip().lower() for r in rows
    )
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


@dataclass
class Coverage:
    """How much of the active book reaches the commission surface, and why not."""

    active_policies: int = 0
    in_ledger: int = 0
    excluded_by_date_floor: int = 0
    excluded_premium: float = 0.0
    excluded_by_status: int = 0
    missing_in_window: int = 0
    missing_in_window_premium: float = 0.0
    since: str = DEFAULT_SINCE
    tombstoned_skipped: int = 0

    @property
    def accounted(self) -> int:
        """Every active policy must be explained by exactly one bucket."""
        return (
            self.in_ledger
            + self.excluded_by_date_floor
            + self.excluded_by_status
            + self.missing_in_window
        )

    @property
    def balanced(self) -> bool:
        """The identity Phase 1 asserts: nothing falls off the map unexplained."""
        return self.accounted == self.active_policies

    def as_dict(self) -> dict[str, Any]:
        return {
            "active_policies": self.active_policies,
            "in_ledger": self.in_ledger,
            "excluded_by_date_floor": {
                "policies": self.excluded_by_date_floor,
                "premium": round(self.excluded_premium, 2),
                "since": self.since,
            },
            "excluded_by_status": self.excluded_by_status,
            "missing_in_window": {
                "policies": self.missing_in_window,
                "premium": round(self.missing_in_window_premium, 2),
            },
            "balanced": self.balanced,
        }


def coverage(
    policies: list[dict[str, Any]],
    ledger_policy_numbers: set[str],
    *,
    since: str | None = None,
) -> Coverage:
    """Classify every active policy into exactly one bucket. Pure.

    Buckets, in precedence order — a policy is counted once:
      in_ledger              already on the surface
      excluded_by_status     not Active/Renewed, so not commissionable
      excluded_by_date_floor effective before the floor: deliberate
      missing_in_window      qualifies but absent — a real gap
    """
    result = Coverage(since=(since or commission_since()))
    floor = _parse_date(result.since)

    for policy in policies:
        if not policy.get("active") or _is_tombstoned(policy):
            if _is_tombstoned(policy):
                result.tombstoned_skipped += 1
            continue

        result.active_policies += 1
        number = str(policy.get("policy_number") or "").strip()

        if number and number in ledger_policy_numbers:
            result.in_ledger += 1
            continue

        status = str(policy.get("status") or "").strip().lower()
        if status not in COMMISSIONABLE_STATUSES:
            result.excluded_by_status += 1
            continue

        effective = _parse_date(policy.get("effective_date"))
        if floor is not None and effective is not None and effective < floor:
            result.excluded_by_date_floor += 1
            result.excluded_premium += _premium(policy)
            continue

        result.missing_in_window += 1
        result.missing_in_window_premium += _premium(policy)

    return result


@dataclass
class Overview:
    rows: list[dict[str, Any]] = field(default_factory=list)
    counts_by_status: dict[str, int] = field(default_factory=dict)
    coverage: Coverage = field(default_factory=Coverage)
    total_ledger_rows: int = 0
    active_overrides: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "commissions": self.rows,
            "count": len(self.rows),
            "total_ledger_rows": self.total_ledger_rows,
            "counts_by_status": self.counts_by_status,
            "coverage": self.coverage.as_dict(),
            "active_overrides": self.active_overrides,
        }


LEDGER_COLUMNS = (
    "id,policy_number,client_name,carrier_name,lob,gross_premium,expected_commission,"
    "actual_commission,delta,reconciliation_status,statement_date"
)


def commission_overview(
    supa: "SupabaseClient",
    *,
    status: str | None = "reconciled",
    limit: int = 1000,
) -> Overview:
    """Ledger rows for *status*, plus the context that stops a blank view lying.

    ``status=None`` or ``"all"`` returns everything. The counts and coverage are
    computed over the WHOLE ledger and book regardless of the filter — that is
    the entire point.
    """
    from hermes.ams import book as ams_book

    all_rows = supa.select(
        LEDGER_TABLE,
        columns=LEDGER_COLUMNS,
        params={"order": "statement_date.desc"},
        limit=max(limit, 5000),
    )

    wanted = (status or "").strip().lower()
    if wanted and wanted != "all":
        rows = [
            r for r in all_rows
            if str(r.get("reconciliation_status") or "").strip().lower() == wanted
        ]
    else:
        rows = list(all_rows)

    try:
        policies = ams_book.select_policies(
            supa,
            columns="policy_number,status,active,effective_date,"
                    "annualized_premium,current_term_amount,premium_amount",
            limit=20000,
        )
    except Exception:  # noqa: BLE001 — coverage is context; never fail the read
        log.exception("commission surface: book read failed; coverage omitted")
        policies = []

    ledger_numbers = {
        str(r.get("policy_number") or "").strip()
        for r in all_rows
        if r.get("policy_number")
    }

    # Human corrections outrank the synced values, and say so on the row.
    try:
        from hermes.overrides.core import apply_overrides
        from hermes.overrides.store import active_overrides

        overrides = active_overrides(supa, ENTITY_TYPE)
    except Exception:  # noqa: BLE001 — a correction layer must not break the read
        log.exception("commission surface: override read failed; showing raw values")
        overrides = {}

    visible = (
        apply_overrides(rows[:limit], overrides,
                        entity_type=ENTITY_TYPE, key_field="policy_number")
        if overrides else rows[:limit]
    )

    return Overview(
        rows=visible,
        counts_by_status=status_counts(all_rows),
        coverage=coverage(policies, ledger_numbers),
        total_ledger_rows=len(all_rows),
        active_overrides=len(overrides),
    )
