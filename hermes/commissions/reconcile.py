"""Commission rollup and classification. Pure except ``run_rollup``.

Reconciliation is **not** data entry. A ledger row's ``actual_commission`` is the
signed sum of its statement transactions — recomputed, never accumulated — and
its status is derived from that sum against the expected. Nobody types a number.

THE GRAIN, which is the thing to get right
------------------------------------------
``commission_ledger`` is one row per policy carrying ONE term's
``expected_commission``. ``commission_transactions`` is a running history of
statement lines — 14 months of them today, 202505 through 202606, with 27 of 30
matched policies spanning more than one month and 21 mixing New Business,
Renewal and endorsement adjustments.

Summing a policy's whole transaction history against one term's expected is
therefore meaningless, and not harmlessly so: measured against production it
classified **23 of 30 rows as overpaid**. That is an artifact of comparing 14
months of income to one term's expectation, not a carrier overpaying. Scoping
the same rollup to the term gives 2 overpaid, 6 reconciled, 18 underpaid — a
distribution that matches how commission actually behaves, since overpayment is
rare and partial payment mid-term is the norm.

So: **actual is the sum of transactions whose ``transaction_date`` falls inside
the ledger row's own policy term.** Everything else on the policy belongs to a
different term and is somebody else's row.

Negative lines — credits, endorsement adjustments, chargebacks — are 30 of the
182 transactions on file. They reduce the term's actual as signed amounts. They
are never a discrepancy in their own right.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

LEDGER_TABLE = "commission_ledger"
TRANSACTIONS_TABLE = "commission_transactions"

# --- statuses (mirrors the DB CHECK added 2026-07-26) ------------------------
PENDING = "pending"
RECONCILED = "reconciled"
UNDERPAID = "underpaid"
OVERPAID = "overpaid"
NO_EXPECTED = "no_expected"
ROLLED_UP = "rolled_up"
CANCELED = "canceled"
MISSING_STATEMENT = "missing_statement"

STATUSES = frozenset({
    PENDING, RECONCILED, UNDERPAID, OVERPAID,
    NO_EXPECTED, ROLLED_UP, CANCELED, MISSING_STATEMENT,
})

# Within a dollar is a match. Carriers and the AMS round differently and a cent
# of drift is not a discrepancy worth anyone's morning.
TOLERANCE = Decimal("1.00")

# Severity bands drive worklist ORDER, never the status itself.
SEVERITY_BANDS = ((Decimal("50"), "low"), (Decimal("200"), "medium"),
                  (Decimal("500"), "high"))
SEVERITY_CRITICAL = "critical"
SEVERITY_MATCHED = "matched"

# Policy lifecycle states that mean the term ended early; a shortfall against a
# full term's expectation is then expected, not a carrier failing to pay.
CANCELLED_STATUSES = frozenset({"cancelled", "canceled", "flat cancel"})


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def term_window(ledger_row: dict[str, Any]) -> tuple[date | None, date | None]:
    """The policy term a ledger row is accountable for.

    Falls back to ``statement_date`` when the policy dates are absent — better a
    one-sided window than silently summing a policy's whole history.
    """
    start = _parse_date(ledger_row.get("policy_effective_date")) or _parse_date(
        ledger_row.get("statement_date")
    )
    return start, _parse_date(ledger_row.get("policy_expiration_date"))


def in_term(transaction: dict[str, Any], start: date | None, end: date | None) -> bool:
    """Is this statement line inside the term? Half-open: [start, end).

    A line dated exactly on the expiration belongs to the NEXT term — that is
    the renewal's first payment, not this term's last.
    """
    when = _parse_date(transaction.get("transaction_date"))
    if when is None:
        return False
    if start is not None and when < start:
        return False
    if end is not None and when >= end:
        return False
    return True


@dataclass
class Rollup:
    """What a ledger row's transactions add up to, within its own term."""

    actual: Decimal | None = None
    transaction_count: int = 0
    in_term_count: int = 0
    negative_count: int = 0
    out_of_term_count: int = 0

    @property
    def has_transactions(self) -> bool:
        return self.in_term_count > 0


def rollup(ledger_row: dict[str, Any], transactions: list[dict[str, Any]]) -> Rollup:
    """Signed sum of in-term transactions. Pure.

    Recomputed from scratch every time, never accumulated onto a stored value,
    so a re-run or a corrected statement converges instead of drifting.
    """
    start, end = term_window(ledger_row)
    result = Rollup(transaction_count=len(transactions))

    total = Decimal("0")
    for txn in transactions:
        if not in_term(txn, start, end):
            result.out_of_term_count += 1
            continue
        amount = _dec(txn.get("commission_amount")) or Decimal("0")
        total += amount
        result.in_term_count += 1
        if amount < 0:
            result.negative_count += 1

    result.actual = total if result.in_term_count else None
    return result


def severity(delta: Decimal | None) -> str:
    """Band an absolute delta. Ordering only — not a status."""
    if delta is None:
        return SEVERITY_MATCHED
    magnitude = abs(delta)
    if magnitude <= TOLERANCE:
        return SEVERITY_MATCHED
    for ceiling, label in SEVERITY_BANDS:
        if magnitude <= ceiling:
            return label
    return SEVERITY_CRITICAL


@dataclass
class Classification:
    status: str
    actual: Decimal | None = None
    expected: Decimal | None = None
    delta: Decimal | None = None
    severity: str = SEVERITY_MATCHED
    reason: str = ""

    def as_update(self) -> dict[str, Any]:
        """The ledger patch. Only derived fields — never touches identity.

        ``delta`` is deliberately absent: commission_ledger.delta is
        GENERATED ALWAYS AS (actual_commission - expected_commission). Postgres
        computes it, and including it errors with 428C9 "can only be updated to
        DEFAULT". We still carry delta on the Classification because that is
        what the status is derived from — we just don't write it back.
        """
        return {
            "actual_commission": float(self.actual) if self.actual is not None else None,
            "reconciliation_status": self.status,
        }


def classify_reconciliation(
    expected: Any,
    roll: Rollup,
    *,
    policy_status: str | None = None,
    term_ended: bool = True,
) -> Classification:
    """Derive the reconciliation state. Pure, and the only place status is decided.

    ``term_ended`` matters: mid-term, a partial payment is simply not finished,
    and calling it ``underpaid`` sends someone chasing a carrier that owes
    nothing yet.
    """
    exp = _dec(expected)
    act = roll.actual

    if not roll.has_transactions:
        return Classification(
            PENDING, None, exp, None, SEVERITY_MATCHED,
            "no statement lines inside this policy term yet",
        )

    if exp is None or exp == 0:
        return Classification(
            NO_EXPECTED, act, exp, None, severity(act),
            "money arrived but no expected commission is on file",
        )

    delta = (act or Decimal("0")) - exp
    band = severity(delta)

    if abs(delta) <= TOLERANCE:
        return Classification(RECONCILED, act, exp, delta, SEVERITY_MATCHED,
                              "actual matches expected within tolerance")

    if delta > 0:
        return Classification(OVERPAID, act, exp, delta, band,
                              "carrier paid more than expected")

    # Short. Why it is short changes who chases it.
    if str(policy_status or "").strip().lower() in CANCELLED_STATUSES:
        return Classification(CANCELED, act, exp, delta, band,
                              "policy cancelled mid-term; shortfall is expected")

    if not term_ended:
        return Classification(MISSING_STATEMENT, act, exp, delta, band,
                              "term still running; more statements expected")

    return Classification(UNDERPAID, act, exp, delta, band,
                          "term ended short of expected commission")


def _index_transactions(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("ledger_id") or "").strip()
        if key:
            out.setdefault(key, []).append(row)
    return out


@dataclass
class RollupRun:
    examined: int = 0
    changed: int = 0
    unchanged: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False
    details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def message(self) -> str:
        counts = " ".join(f"{k}={v}" for k, v in sorted(self.by_status.items()))
        return (
            f"commission rollup ({'dry-run' if self.dry_run else 'live'}): "
            f"examined={self.examined} changed={self.changed} "
            f"unchanged={self.unchanged} | {counts}"
        )


def run_rollup(
    supa: "SupabaseClient",
    *,
    today: date | None = None,
    dry_run: bool = False,
    limit: int = 50000,
) -> RollupRun:
    """Recompute actual/delta/status for every ledger row that has transactions.

    Only rows with at least one transaction are touched — a row nobody has sent
    a statement for keeps whatever it already says rather than being reset.
    """
    today = today or date.today()
    result = RollupRun(dry_run=dry_run)

    ledger = supa.select(LEDGER_TABLE, columns="*", limit=limit)
    txns = supa.select(TRANSACTIONS_TABLE, columns="*", limit=limit)
    by_ledger = _index_transactions(txns)

    for row in ledger:
        ledger_id = str(row.get("id") or "")
        transactions = by_ledger.get(ledger_id)
        if not transactions:
            continue

        result.examined += 1
        roll = rollup(row, transactions)
        _, end = term_window(row)
        term_ended = end is not None and end <= today

        verdict = classify_reconciliation(
            row.get("expected_commission"), roll,
            policy_status=row.get("policy_status"), term_ended=term_ended,
        )
        result.by_status[verdict.status] = result.by_status.get(verdict.status, 0) + 1

        patch = verdict.as_update()
        unchanged = (
            _dec(patch["actual_commission"]) == _dec(row.get("actual_commission"))
            and patch["reconciliation_status"] == row.get("reconciliation_status")
        )
        if unchanged:
            result.unchanged += 1
            continue

        result.changed += 1
        result.details.append({
            "policy_number": row.get("policy_number"),
            "from": row.get("reconciliation_status"),
            "to": verdict.status,
            "actual": patch["actual_commission"],
            "expected": row.get("expected_commission"),
            "severity": verdict.severity,
        })
        if not dry_run:
            supa.update(LEDGER_TABLE, ledger_id, patch)

    log.info("%s", result.message)
    return result
