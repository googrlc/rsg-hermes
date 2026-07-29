"""Finance Desk provenance — a commission figure never travels without its origin.

`origin='statement'` means a carrier statement backs the number. `origin='seed'`
means it was backfilled from NowCerts with a computed expectation and nothing ever
matched it — a statement gap, not a proven shortpay. The desk persona forbids
quoting one as the other, which is only enforceable if the tools carry the split.
"""
from __future__ import annotations

from hermes.core import nl_agent as A


class FakeSupa:
    def __init__(self, rows):
        self._rows = rows
        self.last = None

    def select(self, table, *, columns="*", params=None, limit=100):
        self.last = (table, columns, params or {})
        return self._rows


LEDGER = [
    # statement-backed: money a carrier actually reported
    {"client_name": "Exquisite Delites", "carrier_name": "PROGRESSIVE MOUNTAIN INS CO",
     "policy_number": "P-1", "expected_commission": 500, "actual_commission": 400,
     "reconciliation_status": "underpaid", "origin": "statement"},
    # seed: computed expectation, no statement ever matched
    {"client_name": "Ambitious 4 Logistics", "carrier_name": "GEICO MARINE",
     "policy_number": "P-2", "expected_commission": 1000, "actual_commission": 0,
     "reconciliation_status": "missing_statement", "origin": "seed"},
    {"client_name": "Sandra Centeno", "carrier_name": "CNA",
     "policy_number": "P-3", "expected_commission": 200, "actual_commission": 0,
     "reconciliation_status": "reconciled", "origin": "seed"},
]


def _patch(monkeypatch, rows=LEDGER):
    import hermes.integrations.supabase_client as sc
    fake = FakeSupa(rows)
    monkeypatch.setattr(sc, "SupabaseClient", lambda *a, **k: fake)
    return fake


def test_summary_reports_origin_split(monkeypatch):
    fake = _patch(monkeypatch)
    res = A._exec_commission_summary({})
    assert res.ok
    assert "origin" in fake.last[1], "the summary must read the provenance column"
    # Statement-backed money is quoted separately from unproven seed expectation.
    assert res.data["by_origin"]["statement"] == {"expected": 500.0, "received": 400.0, "rows": 1.0}
    assert res.data["by_origin"]["seed"] == {"expected": 1200.0, "received": 0.0, "rows": 2.0}
    assert "By origin — statement:" in res.message  # statement-backed leads
    assert "seed:" in res.message


def test_summary_labels_rows_with_no_origin(monkeypatch):
    _patch(monkeypatch, [{"expected_commission": 100, "actual_commission": 0, "origin": None}])
    res = A._exec_commission_summary({})
    # An unstamped row is never silently counted as statement-backed.
    assert "unknown" in res.data["by_origin"] and "statement" not in res.data["by_origin"]


def test_shortfalls_tag_each_row_and_split_the_total(monkeypatch):
    fake = _patch(monkeypatch)
    res = A._exec_commission_shortfalls({})
    assert res.ok
    assert "origin" in fake.last[1]
    # reconciled rows are not owed; the other two are, biggest first.
    assert res.data["count"] == 2
    assert res.data["seed_total"] == 1000.0        # can't prove payment
    assert res.data["statement_total"] == 100.0    # carrier actually shorted us
    assert "origin=seed" in res.message and "origin=statement" in res.message
