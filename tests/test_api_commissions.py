"""API tests for the commissions read.

Pins the contract the cockpit depends on: an empty result still carries the
counts and the coverage, so the view can explain itself instead of rendering a
blank table.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app


@pytest.fixture(autouse=True)
def _reset_singletons():
    import hermes.api as api_mod
    api_mod._supa = None
    yield
    api_mod._supa = None


@pytest.fixture
def client():
    return TestClient(app)


class FakeSupa:
    def __init__(self, ledger):
        self._ledger = ledger

    def select(self, table, *, columns="*", params=None, limit=100):
        assert table == "commission_ledger"
        return [dict(r) for r in self._ledger][:limit]


LEDGER = [
    {"policy_number": "A", "reconciliation_status": "pending", "expected_commission": 100},
    {"policy_number": "B", "reconciliation_status": "pending", "expected_commission": 200},
    {"policy_number": "C", "reconciliation_status": "underpaid", "expected_commission": 300},
]

BOOK = [
    {"policy_number": "A", "status": "Active", "active": True,
     "effective_date": "2026-03-01", "annualized_premium": 1000},
    {"policy_number": "Z", "status": "Active", "active": True,
     "effective_date": "2025-02-01", "annualized_premium": 5000},   # pre-floor
]


def _run(client, url, ledger=LEDGER, book=BOOK):
    with patch("hermes.api._get_supa", return_value=FakeSupa(ledger)), \
         patch("hermes.ams.book.select_policies", return_value=book):
        return client.get(url)


def test_empty_result_still_reports_what_exists(client):
    """The original bug: zero reconciled rows rendered as 'no data'."""
    r = _run(client, "/api/commissions")
    assert r.status_code == 200
    body = r.json()
    assert body["commissions"] == []
    assert body["count"] == 0
    assert body["total_ledger_rows"] == 3
    assert body["counts_by_status"] == {"pending": 2, "underpaid": 1}


def test_coverage_reports_the_deliberate_exclusion(client):
    body = _run(client, "/api/commissions").json()
    floor = body["coverage"]["excluded_by_date_floor"]
    assert floor["policies"] == 1
    assert floor["premium"] == 5000.0
    assert body["coverage"]["in_ledger"] == 1
    assert body["coverage"]["balanced"] is True


def test_status_all_returns_every_row(client):
    body = _run(client, "/api/commissions?status=all").json()
    assert body["count"] == 3


def test_status_filter_selects(client):
    body = _run(client, "/api/commissions?status=underpaid").json()
    assert body["count"] == 1
    assert body["commissions"][0]["policy_number"] == "C"


def test_limit_applies_to_rows_not_to_the_counts(client):
    body = _run(client, "/api/commissions?status=all&limit=1").json()
    assert body["count"] == 1
    assert body["total_ledger_rows"] == 3          # counts still see everything
    assert body["counts_by_status"]["pending"] == 2


def test_book_failure_degrades_to_rows_without_coverage(client):
    with patch("hermes.api._get_supa", return_value=FakeSupa(LEDGER)), \
         patch("hermes.ams.book.select_policies", side_effect=RuntimeError("AMS down")):
        body = client.get("/api/commissions?status=all").json()
    assert body["count"] == 3
    assert body["coverage"]["active_policies"] == 0


def test_ledger_failure_is_a_502_not_a_silent_empty(client):
    class Boom:
        def select(self, *a, **k):
            raise RuntimeError("supabase down")

    with patch("hermes.api._get_supa", return_value=Boom()):
        r = client.get("/api/commissions")
    assert r.status_code == 502
