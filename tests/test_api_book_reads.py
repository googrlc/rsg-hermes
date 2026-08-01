"""Tests for the book-read + workspace-stats API endpoints (CRM cockpit views)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app


@pytest.fixture(autouse=True)
def _reset_singletons():
    from hermes_app import deps
    deps.reset_clients()
    yield
    deps.reset_clients()


@pytest.fixture
def client():
    return TestClient(app)


class FakeSupa:
    def __init__(self, tables):
        self.tables = tables

    def select(self, table, *, columns="*", params=None, limit=100):
        rows = self.tables.get(table, [])
        for k, v in (params or {}).items():
            if k == "order":
                continue
            if isinstance(v, str) and v.startswith("eq."):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
            elif isinstance(v, str) and v.startswith("neq."):
                rows = [r for r in rows if str(r.get(k)) != v[4:]]
        return [dict(r) for r in rows][:limit]


def _patch(supa):
    return patch("hermes_app.deps.get_supa", return_value=supa)


def test_list_clients(client):
    supa = FakeSupa({"canonical_clients": [
        {"nowcerts_insured_guid": "g1", "insured_name": "Acme LLC"},
        {"nowcerts_insured_guid": "g2", "insured_name": "Beta Inc"},
    ]})
    with _patch(supa):
        r = client.get("/api/clients")
    assert r.status_code == 200 and r.json()["count"] == 2


def test_list_policies(client):
    supa = FakeSupa({"canonical_policies": [
        {"policy_number": "P1", "annualized_premium": 1000},
    ]})
    with _patch(supa):
        r = client.get("/api/policies")
    assert r.status_code == 200 and r.json()["count"] == 1


def test_list_commissions(client):
    """A row with no reconciliation_status is not 'reconciled', so the default
    filter returns nothing — but the response still reports that the row exists.

    This test previously asserted count == 1 against the default filter and had
    been failing; it encoded the bug where an empty view looked like an empty
    ledger. Fixed with the endpoint 2026-07-26.
    """
    supa = FakeSupa({"commission_ledger": [
        {"policy_number": "P1", "expected_commission": 150},
    ]})
    with _patch(supa), patch("hermes_core.book.select_policies", return_value=[]):
        r = client.get("/api/commissions")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0                    # nothing is reconciled
    assert body["total_ledger_rows"] == 1        # but the ledger is not empty
    assert body["counts_by_status"] == {"unknown": 1}

    with _patch(supa), patch("hermes_core.book.select_policies", return_value=[]):
        r = client.get("/api/commissions?status=all")
    assert r.json()["count"] == 1


def test_workspace_stats(client):
    supa = FakeSupa({
        "canonical_clients": [{"nowcerts_insured_guid": f"g{i}"} for i in range(3)],
        "canonical_policies": [{"annualized_premium": 1000}, {"annualized_premium": 500}],
        "project_85_renewals": [{"id": "r1"}],
        "opportunities": [{"id": "o1", "status": "open"}, {"id": "o2", "status": "won"}],
        "agency_crm_cases": [{"id": "c1", "status": "open"}],
        "agency_crm_tasks": [{"id": "t1", "status": "not_started"}, {"id": "t2", "status": "completed"}],
        "commission_ledger": [{"id": "l1"}],
    })
    with _patch(supa):
        r = client.get("/api/workspace-stats")
    assert r.status_code == 200
    body = r.json()
    assert body["clients"] == 3
    assert body["policies"] == 2
    assert body["annualized_premium"] == 1500.0
    assert body["renewals"] == 1
    assert body["pipeline"] == 1              # only open opps
    assert body["open_cases"] == 1
    assert body["open_tasks"] == 1            # excludes completed
    assert body["commissions"] == 1
