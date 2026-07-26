"""Tests for the commission rollup and classification.

The load-bearing decision here is the GRAIN. commission_ledger holds one term's
expected; commission_transactions holds 14 months of statement history. Summing
the whole history against one term measured 23 of 30 production rows as
"overpaid" — an artifact, not a carrier overpaying. Term-scoping gives 2. The
first two test classes exist to keep that fix from being undone.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from hermes.commissions import reconcile as rc

TODAY = date(2026, 7, 26)


def led(**kw):
    base = {
        "id": "L1", "policy_number": "P1", "expected_commission": 1000,
        "policy_effective_date": "2026-01-01", "policy_expiration_date": "2027-01-01",
        "statement_date": "2026-01-01", "policy_status": "Active",
    }
    base.update(kw)
    return base


def txn(amount, when="2026-03-01", **kw):
    base = {"ledger_id": "L1", "commission_amount": amount, "transaction_date": when}
    base.update(kw)
    return base


# --- the term window ---------------------------------------------------------

class TestTermWindow:
    def test_uses_the_policy_dates(self):
        assert rc.term_window(led()) == (date(2026, 1, 1), date(2027, 1, 1))

    def test_falls_back_to_statement_date_when_policy_dates_are_absent(self):
        start, end = rc.term_window(led(policy_effective_date=None,
                                        policy_expiration_date=None,
                                        statement_date="2026-05-05"))
        assert start == date(2026, 5, 5) and end is None

    def test_window_is_half_open_so_a_renewal_does_not_land_in_the_old_term(self):
        start, end = date(2026, 1, 1), date(2027, 1, 1)
        assert rc.in_term(txn(10, "2026-12-31"), start, end)
        assert not rc.in_term(txn(10, "2027-01-01"), start, end)   # the renewal

    def test_before_the_term_is_out(self):
        assert not rc.in_term(txn(10, "2025-12-31"), date(2026, 1, 1), date(2027, 1, 1))

    def test_a_line_with_no_date_is_out(self):
        assert not rc.in_term(txn(10, None), date(2026, 1, 1), None)

    def test_open_ended_term_accepts_anything_after_the_start(self):
        assert rc.in_term(txn(10, "2030-01-01"), date(2026, 1, 1), None)


# --- the rollup --------------------------------------------------------------

class TestRollup:
    def test_sums_only_in_term_lines(self):
        r = rc.rollup(led(), [
            txn(100, "2026-03-01"),      # in
            txn(200, "2026-06-01"),      # in
            txn(999, "2025-06-01"),      # before the term
            txn(999, "2027-06-01"),      # after the term
        ])
        assert r.actual == Decimal("300")
        assert r.in_term_count == 2
        assert r.out_of_term_count == 2
        assert r.transaction_count == 4

    def test_the_production_shape_that_forced_term_scoping(self):
        """14 months of history against one term. Lifetime sum would be 1500 and
        read as overpaid; the term only owns 900."""
        r = rc.rollup(led(expected_commission=1000), [
            txn(300, "2025-08-01"),   # prior term
            txn(300, "2025-11-01"),   # prior term
            txn(400, "2026-02-01"),   # this term
            txn(500, "2026-05-01"),   # this term
        ])
        assert r.actual == Decimal("900")
        verdict = rc.classify_reconciliation(1000, r)
        assert verdict.status == rc.MISSING_STATEMENT or verdict.status == rc.UNDERPAID
        assert verdict.status != rc.OVERPAID

    def test_negatives_reduce_the_total_and_are_counted(self):
        r = rc.rollup(led(), [txn(500, "2026-02-01"), txn(-75.50, "2026-03-01")])
        assert r.actual == Decimal("424.50")
        assert r.negative_count == 1

    def test_no_in_term_lines_means_no_actual_not_zero(self):
        """None and 0 are different: one is 'nothing arrived', the other is
        'a credit cancelled it out'."""
        r = rc.rollup(led(), [txn(100, "2020-01-01")])
        assert r.actual is None
        assert not r.has_transactions

    def test_credits_netting_to_zero_still_counts_as_having_transactions(self):
        r = rc.rollup(led(), [txn(100, "2026-02-01"), txn(-100, "2026-03-01")])
        assert r.actual == Decimal("0")
        assert r.has_transactions

    def test_unparseable_amount_is_treated_as_zero_not_a_crash(self):
        r = rc.rollup(led(), [txn("junk", "2026-02-01")])
        assert r.actual == Decimal("0") and r.in_term_count == 1


# --- classification ----------------------------------------------------------

class TestClassify:
    def test_no_transactions_is_pending(self):
        v = rc.classify_reconciliation(1000, rc.Rollup())
        assert v.status == rc.PENDING and v.delta is None

    def test_within_a_dollar_is_reconciled(self):
        r = rc.rollup(led(), [txn(1000.40, "2026-02-01")])
        assert rc.classify_reconciliation(1000, r).status == rc.RECONCILED

    def test_just_over_a_dollar_is_not(self):
        r = rc.rollup(led(), [txn(1001.50, "2026-02-01")])
        assert rc.classify_reconciliation(1000, r).status == rc.OVERPAID

    def test_money_with_no_expected_on_file(self):
        r = rc.rollup(led(), [txn(500, "2026-02-01")])
        v = rc.classify_reconciliation(None, r)
        assert v.status == rc.NO_EXPECTED and v.delta is None

    def test_zero_expected_is_treated_as_none(self):
        r = rc.rollup(led(), [txn(500, "2026-02-01")])
        assert rc.classify_reconciliation(0, r).status == rc.NO_EXPECTED

    def test_short_after_the_term_ended_is_underpaid(self):
        r = rc.rollup(led(), [txn(600, "2026-02-01")])
        v = rc.classify_reconciliation(1000, r, term_ended=True)
        assert v.status == rc.UNDERPAID and v.delta == Decimal("-400")

    def test_short_mid_term_is_missing_statement_not_underpaid(self):
        """Chasing a carrier that does not owe anything yet is the failure."""
        r = rc.rollup(led(), [txn(600, "2026-02-01")])
        assert rc.classify_reconciliation(1000, r, term_ended=False).status == rc.MISSING_STATEMENT

    def test_short_on_a_cancelled_policy_is_canceled(self):
        r = rc.rollup(led(), [txn(300, "2026-02-01")])
        v = rc.classify_reconciliation(1000, r, policy_status="Cancelled", term_ended=True)
        assert v.status == rc.CANCELED

    def test_cancelled_beats_mid_term(self):
        r = rc.rollup(led(), [txn(300, "2026-02-01")])
        v = rc.classify_reconciliation(1000, r, policy_status="Flat Cancel", term_ended=False)
        assert v.status == rc.CANCELED

    def test_overpaid_ignores_term_and_cancellation(self):
        r = rc.rollup(led(), [txn(1500, "2026-02-01")])
        v = rc.classify_reconciliation(1000, r, policy_status="Cancelled", term_ended=False)
        assert v.status == rc.OVERPAID

    def test_every_status_is_one_the_database_accepts(self):
        for status in (rc.PENDING, rc.RECONCILED, rc.UNDERPAID, rc.OVERPAID,
                       rc.NO_EXPECTED, rc.CANCELED, rc.MISSING_STATEMENT):
            assert status in rc.STATUSES

    def test_as_update_only_touches_derived_fields(self):
        r = rc.rollup(led(), [txn(1000, "2026-02-01")])
        patch = rc.classify_reconciliation(1000, r).as_update()
        assert set(patch) == {"actual_commission", "reconciliation_status"}

    def test_as_update_never_writes_the_generated_delta_column(self):
        """commission_ledger.delta is GENERATED ALWAYS AS (actual - expected).
        Writing it fails with 428C9. Caught live 2026-07-26."""
        r = rc.rollup(led(), [txn(600, "2026-02-01")])
        v = rc.classify_reconciliation(1000, r, term_ended=True)
        assert v.delta == Decimal("-400")        # still computed, for the status
        assert "delta" not in v.as_update()      # but never written back


# --- severity ----------------------------------------------------------------

@pytest.mark.parametrize("delta,band", [
    (None, "matched"), (Decimal("0.50"), "matched"), (Decimal("-1.00"), "matched"),
    (Decimal("25"), "low"), (Decimal("-120"), "medium"),
    (Decimal("300"), "high"), (Decimal("-5000"), "critical"),
])
def test_severity_bands(delta, band):
    assert rc.severity(delta) == band


def test_severity_is_ordering_not_status():
    r = rc.rollup(led(), [txn(400, "2026-02-01")])
    v = rc.classify_reconciliation(1000, r, term_ended=True)
    assert v.status == rc.UNDERPAID and v.severity == "critical"


# --- the driver --------------------------------------------------------------

class FakeSupa:
    def __init__(self, ledger, txns):
        self.tables = {rc.LEDGER_TABLE: ledger, rc.TRANSACTIONS_TABLE: txns}
        self.updates: list[tuple[str, dict]] = []

    def select(self, table, *, columns="*", params=None, limit=1000):
        return [dict(r) for r in self.tables.get(table, [])][:limit]

    def update(self, table, record_id, payload):
        self.updates.append((record_id, payload))
        return {"id": record_id, **payload}


def test_run_rollup_updates_only_rows_that_changed():
    supa = FakeSupa([led(id="L1"), led(id="L2", policy_number="P2")],
                    [txn(1000, "2026-02-01", ledger_id="L1")])
    out = rc.run_rollup(supa, today=TODAY)
    assert out.examined == 1            # L2 has no transactions — untouched
    assert out.changed == 1
    assert out.by_status == {rc.RECONCILED: 1}
    assert supa.updates[0][0] == "L1"


def test_run_rollup_is_idempotent():
    row = led(id="L1", actual_commission=1000.0, delta=0.0,
              reconciliation_status=rc.RECONCILED)
    supa = FakeSupa([row], [txn(1000, "2026-02-01", ledger_id="L1")])
    out = rc.run_rollup(supa, today=TODAY)
    assert out.unchanged == 1 and out.changed == 0
    assert supa.updates == []


def test_dry_run_writes_nothing_but_still_reports():
    supa = FakeSupa([led(id="L1")], [txn(1000, "2026-02-01", ledger_id="L1")])
    out = rc.run_rollup(supa, today=TODAY, dry_run=True)
    assert out.changed == 1 and supa.updates == []
    assert "dry-run" in out.message


def test_rows_with_no_transactions_keep_their_existing_status():
    supa = FakeSupa([led(id="L1", reconciliation_status="rolled_up")], [])
    out = rc.run_rollup(supa, today=TODAY)
    assert out.examined == 0 and supa.updates == []


def test_term_ended_is_derived_from_the_expiration_date():
    # Term ends 2026-02-01, today is 2026-07-26 -> ended -> short means underpaid.
    supa = FakeSupa(
        [led(id="L1", policy_expiration_date="2026-02-01", expected_commission=1000)],
        [txn(600, "2026-01-15", ledger_id="L1")],
    )
    out = rc.run_rollup(supa, today=TODAY)
    assert out.by_status == {rc.UNDERPAID: 1}


def test_term_still_running_reports_missing_statement():
    supa = FakeSupa(
        [led(id="L1", policy_expiration_date="2027-01-01", expected_commission=1000)],
        [txn(600, "2026-02-01", ledger_id="L1")],
    )
    out = rc.run_rollup(supa, today=TODAY)
    assert out.by_status == {rc.MISSING_STATEMENT: 1}
