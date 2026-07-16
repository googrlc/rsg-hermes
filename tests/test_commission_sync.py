"""Tests for the canonical book → commission_ledger expected-value seeding."""
from __future__ import annotations

from typing import Any

from hermes.sync import commission_sync as cs


# --- fakes -------------------------------------------------------------------
class FakeSupabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = tables or {}
        self._n = 0

    def select(self, table, *, columns="*", params=None, limit=100):
        rows = self.tables.get(table, [])
        for k, v in (params or {}).items():
            if isinstance(v, str) and v.startswith("eq."):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
        return [dict(r) for r in rows][:limit]

    def insert(self, table, payload):
        self._n += 1
        row = {"id": f"led-{self._n}", **payload}
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def update(self, table, record_id, payload):
        for r in self.tables.get(table, []):
            if str(r.get("id")) == str(record_id):
                r.update(payload)
                return dict(r)
        raise AssertionError(f"update: no {table} row {record_id}")


RULE = {"id": "rule-1", "carrier_name": "Acme Mutual", "lob": "General Liability",
        "nb_percent": 15, "renewal_percent": 12, "commission_basis": "gross", "active": True}


def cpol(policy_number="P1", *, guid=None, insured="ins1", status="Active",
         carrier="Acme Mutual", lob="General Liability", premium=1000.0,
         agency_commission_amount=None, eff="2026-01-01", exp="2027-01-01", state="GA"):
    return {
        "policy_number": policy_number, "policy_guid": guid or f"pg-{policy_number}",
        "nowcerts_insured_guid": insured, "status": status, "carrier": carrier,
        "lines_of_business": lob, "premium_amount": premium,
        "annualized_premium": premium, "agency_commission_amount": agency_commission_amount,
        "effective_date": eff, "expiration_date": exp, "state": state,
    }


def supa_with(policies, *, ledger=None, clients=None, rules=RULE):
    return FakeSupabase({
        "canonical_policies": policies,
        "canonical_clients": clients if clients is not None else [
            {"nowcerts_insured_guid": "ins1", "insured_name": "Acme LLC"}],
        "commission_rules": [rules] if isinstance(rules, dict) else (rules or []),
        "commission_ledger": ledger or [],
    })


def _led_row(**over):
    """A realistic commission_ledger row (all live columns present)."""
    row = {
        "id": "led-x", "policy_number": "P1", "nowcerts_policy_id": None,
        "carrier_name": "Old", "lob": "General Liability", "client_name": "Old",
        "statement_date": "2026-06-01", "policy_effective_date": "2026-01-01",
        "policy_expiration_date": "2027-01-01", "is_renewal": False,
        "gross_premium": 500.0, "expected_commission": 50.0, "actual_commission": 137.0,
        "delta": 87.0, "reconciliation_status": "reconciled", "payment_received": True,
        "statement_source": "carrier_statement", "commission_rule_id": None,
        "commission_basis": None, "state": "GA", "updated_at": "2026-06-01", "notes": "paid",
    }
    row.update(over)
    return row


# --- tests -------------------------------------------------------------------
def test_new_policy_seeds_expected_from_rule():
    supa = supa_with([cpol("P1", premium=2000.0)])
    res = cs.run_commission_sync(supa)
    assert res.inserted == 1 and res.updated == 0
    row = supa.tables[cs.LEDGER_TABLE][0]
    assert row["policy_number"] == "P1"
    assert row["expected_commission"] == 300.0        # 2000 * 15% new-business
    assert row["gross_premium"] == 2000.0
    assert row["client_name"] == "Acme LLC"
    assert row["nowcerts_policy_id"] == "pg-P1"        # real policy-level guid
    assert row["reconciliation_status"] == "pending"
    assert row["statement_source"] == cs.STATEMENT_SOURCE


def test_direct_agency_commission_preferred_over_rule():
    supa = supa_with([cpol("P1", premium=2000.0, agency_commission_amount=275.0)])
    cs.run_commission_sync(supa)
    row = supa.tables[cs.LEDGER_TABLE][0]
    assert row["expected_commission"] == 275.0        # NowCerts direct, not 300 rule


def test_renewal_uses_renewal_rate():
    supa = supa_with([cpol("P1", premium=2000.0, status="Renewed")])
    cs.run_commission_sync(supa)
    row = supa.tables[cs.LEDGER_TABLE][0]
    assert row["is_renewal"] is True
    assert row["expected_commission"] == 240.0        # 2000 * 12% renewal


def test_existing_row_refreshes_expected_but_preserves_actuals():
    supa = supa_with([cpol("P1", premium=3000.0, agency_commission_amount=450.0)],
                     ledger=[_led_row(policy_number="P1")])
    res = cs.run_commission_sync(supa)
    assert res.updated == 1 and res.inserted == 0
    row = supa.tables[cs.LEDGER_TABLE][0]
    # expected side refreshed
    assert row["gross_premium"] == 3000.0
    assert row["expected_commission"] == 450.0
    assert row["nowcerts_policy_id"] == "pg-P1"
    # statement-sourced actuals PRESERVED (never overwritten)
    assert row["actual_commission"] == 137.0
    assert row["reconciliation_status"] == "reconciled"
    assert row["payment_received"] is True
    assert row["statement_source"] == "carrier_statement"
    assert row["statement_date"] == "2026-06-01"


def test_non_commissionable_status_skipped():
    supa = supa_with([cpol("P1", status="Cancelled")])
    res = cs.run_commission_sync(supa)
    assert res.skipped_not_commissionable == 1 and res.inserted == 0


def test_zero_premium_skipped():
    supa = supa_with([cpol("P1", premium=0.0)])
    res = cs.run_commission_sync(supa)
    assert res.skipped_no_premium == 1 and res.inserted == 0


def test_no_rule_and_no_direct_still_inserts_with_null_expected():
    supa = supa_with([cpol("P1", carrier="Unknown Co", lob="Mystery")], rules=[])
    res = cs.run_commission_sync(supa)
    assert res.inserted == 1 and res.no_expected == 1
    assert supa.tables[cs.LEDGER_TABLE][0].get("expected_commission") is None


def test_dry_run_no_writes():
    supa = supa_with([cpol("P1")])
    res = cs.run_commission_sync(supa, dry_run=True)
    assert res.inserted == 1
    assert supa.tables.get(cs.LEDGER_TABLE, []) == []


def test_limit_caps_policies():
    supa = supa_with([cpol(f"P{i}", insured="ins1") for i in range(5)])
    res = cs.run_commission_sync(supa, limit=2)
    assert res.policies_scanned == 2


def test_future_effective_excluded():
    # A 2027-effective policy is a staged renewal — not won yet — so it must not ledger.
    supa = supa_with([cpol("P1", eff="2027-06-01", exp="2028-06-01")])
    res = cs.run_commission_sync(supa)
    assert res.skipped_out_of_window == 1 and res.inserted == 0
    assert supa.tables.get(cs.LEDGER_TABLE, []) == []


def test_pre_since_old_book_excluded():
    supa = supa_with([cpol("P1", eff="2025-06-01", exp="2026-06-01")])
    res = cs.run_commission_sync(supa, since="2026-01-01")
    assert res.skipped_out_of_window == 1 and res.inserted == 0
