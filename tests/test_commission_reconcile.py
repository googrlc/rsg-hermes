"""Tests for statement reconciliation against commission_ledger (Phase 3a).

Runs real CSV statements through the reused parser and a fake Supabase to assert
ledger write-back, discrepancy-row creation, dedup idempotency, and dry-run.
"""

from __future__ import annotations

import pytest

from hermes.commissions import config, reconcile


LEDGER = [
    {"id": "L1", "policy_number": "WC-001", "carrier_name": "AmTrust",
     "client_name": "Acme LLC", "expected_commission": 1800.0, "statement_date": "2026-03-01"},
    {"id": "L2", "policy_number": "BOP-77", "carrier_name": "Attune",
     "client_name": "Zeta Co", "expected_commission": 500.0, "statement_date": "2026-03-01"},
]


class FakeSupabase:
    def __init__(self, ledger=LEDGER, open_recon=()):
        self._ledger = list(ledger)
        self._open_recon = list(open_recon)
        self.updates: list[tuple] = []
        self.inserts: list[dict] = []

    def select(self, table, *, columns="*", params=None, limit=100):
        if table == config.LEDGER_TABLE and not (params and "status" in params):
            return list(self._ledger)
        if table == config.RECON_TABLE:
            return list(self._open_recon)
        return []

    def update(self, table, record_id, payload):
        self.updates.append((table, record_id, payload))
        return payload

    def insert(self, table, payload):
        self.inserts.append(payload)
        return payload


class FakeSlack:
    def __init__(self):
        self.posts: list[str] = []

    def post_message(self, *, text, blocks=None):
        self.posts.append(text)
        return {"ok": True}


def _csv(tmp_path, body: str):
    p = tmp_path / "statement.csv"
    p.write_text(body)
    return str(p)


def _recon_inserts(supa):
    return [p for p in supa.inserts]  # only insert() is used for recon rows


# --- Matching & ledger write-back ------------------------------------------

def test_exact_match_updates_ledger_no_discrepancy(tmp_path):
    supa = FakeSupabase()
    stmt = _csv(tmp_path, "policy,carrier,commission paid\nWC-001,AmTrust,1800.00\n")
    res = reconcile.run_reconciliation(supa, stmt, notifier=FakeSlack(), statement_date="2026-06-30")
    assert res.matched == 1 and res.discrepancies == 0
    assert supa.updates[0][1] == "L1"
    assert supa.updates[0][2]["actual_commission"] == 1800.0
    assert supa.updates[0][2]["delta"] == 0.0
    assert supa.updates[0][2]["reconciliation_status"] == "reconciled"
    assert supa.inserts == []  # within tolerance → no queue row


def test_shortage_flags_discrepancy_row(tmp_path):
    supa = FakeSupabase()
    stmt = _csv(tmp_path, "policy,carrier,commission paid\nBOP-77,Attune,450.00\n")
    res = reconcile.run_reconciliation(supa, stmt, notifier=FakeSlack(), statement_date="2026-06-30")
    assert res.matched == 1 and res.discrepancies == 1
    assert res.total_short == 50.0
    row = supa.inserts[0]
    assert row["discrepancy_type"] == "short"
    assert row["delta"] == -50.0
    assert row["priority"] == "low"      # |50| < 100
    assert row["assigned_to"] == "Gretchen"
    assert row["ledger_id"] == "L2"


def test_unmatched_line_creates_unmatched_row(tmp_path):
    supa = FakeSupabase()
    stmt = _csv(tmp_path, "policy,carrier,commission paid\nUNKNOWN-9,Foo,120.00\n")
    res = reconcile.run_reconciliation(supa, stmt, notifier=FakeSlack(), statement_date="2026-06-30")
    assert res.unmatched == 1 and res.matched == 0
    row = supa.inserts[0]
    assert row["discrepancy_type"] == "unmatched_statement_line"
    assert row["priority"] == "medium"   # |120| >= 100
    assert row["policy_number"] == "UNKNOWN-9"


def test_full_statement_counts(tmp_path):
    supa = FakeSupabase()
    stmt = _csv(
        tmp_path,
        "policy,carrier,commission paid\n"
        "WC-001,AmTrust,1800.00\n"   # exact
        "BOP-77,Attune,450.00\n"      # short 50
        "UNKNOWN-9,Foo,120.00\n",     # unmatched
    )
    res = reconcile.run_reconciliation(supa, stmt, notifier=FakeSlack(), statement_date="2026-06-30")
    assert res.parsed == 3
    assert res.matched == 2 and res.unmatched == 1
    assert res.discrepancies == 2      # short + unmatched
    assert res.total_short == 50.0
    assert res.ok


def test_policy_number_matching_is_normalized(tmp_path):
    supa = FakeSupabase()
    # statement uses a space where the ledger uses a hyphen
    stmt = _csv(tmp_path, "policy,carrier,commission paid\nWC 001,AmTrust,1800.00\n")
    res = reconcile.run_reconciliation(supa, stmt, notifier=FakeSlack(), statement_date="2026-06-30")
    assert res.matched == 1
    assert supa.updates[0][1] == "L1"


def test_priority_buckets():
    assert reconcile._priority(600) == "high"
    assert reconcile._priority(150) == "medium"
    assert reconcile._priority(20) == "low"


# --- Idempotency / dedup ---------------------------------------------------

def test_rerun_dedups_existing_open_row(tmp_path):
    open_recon = [{"ledger_id": "L2", "policy_number": "BOP-77",
                   "statement_date": "2026-06-30", "status": "open"}]
    supa = FakeSupabase(open_recon=open_recon)
    stmt = _csv(tmp_path, "policy,carrier,commission paid\nBOP-77,Attune,450.00\n")
    res = reconcile.run_reconciliation(supa, stmt, notifier=FakeSlack(), statement_date="2026-06-30")
    assert res.duplicates_skipped == 1
    assert res.discrepancies == 0
    assert supa.inserts == []            # no duplicate queue row
    assert supa.updates[0][1] == "L2"    # ledger still updated (idempotent no-op)


# --- Dry run & empties -----------------------------------------------------

def test_dry_run_writes_nothing(tmp_path):
    supa = FakeSupabase()
    slack = FakeSlack()
    stmt = _csv(tmp_path, "policy,carrier,commission paid\nBOP-77,Attune,450.00\n")
    res = reconcile.run_reconciliation(supa, stmt, notifier=slack, dry_run=True, statement_date="2026-06-30")
    assert res.discrepancies == 1        # would-flag
    assert supa.updates == [] and supa.inserts == []
    assert slack.posts == []


def test_empty_statement_is_ok_with_zero_rows(tmp_path):
    supa = FakeSupabase()
    stmt = _csv(tmp_path, "policy,carrier,commission paid\n")
    res = reconcile.run_reconciliation(supa, stmt, notifier=FakeSlack(), statement_date="2026-06-30")
    assert res.parsed == 0 and res.ok
    assert "0 rows" in res.message


def test_posts_one_line_slack_summary(tmp_path):
    supa = FakeSupabase()
    slack = FakeSlack()
    stmt = _csv(tmp_path, "policy,carrier,commission paid\nBOP-77,Attune,450.00\n")
    reconcile.run_reconciliation(supa, stmt, notifier=slack, statement_date="2026-06-30")
    assert len(slack.posts) == 1
    assert "Commission reconciliation" in slack.posts[0]
