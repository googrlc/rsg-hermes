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
    from hermes.routers import deps
    deps.reset_clients()
    yield
    deps.reset_clients()


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
    with patch("hermes.routers.deps.get_supa", return_value=FakeSupa(ledger)), \
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
    with patch("hermes.routers.deps.get_supa", return_value=FakeSupa(LEDGER)), \
         patch("hermes.ams.book.select_policies", side_effect=RuntimeError("AMS down")):
        body = client.get("/api/commissions?status=all").json()
    assert body["count"] == 3
    assert body["coverage"]["active_policies"] == 0


def test_ledger_failure_is_a_502_not_a_silent_empty(client):
    class Boom:
        def select(self, *a, **k):
            raise RuntimeError("supabase down")

    with patch("hermes.routers.deps.get_supa", return_value=Boom()):
        r = client.get("/api/commissions")
    assert r.status_code == 502


# --- overrides ---------------------------------------------------------------

class OverrideSupa:
    """Ledger + users + a writable portal_overrides/portal_write_log."""

    def __init__(self, ledger):
        self.tables = {
            "commission_ledger": ledger,
            "agency_crm_users": [
                {"email": "lamar@risksolutionsgroup.net", "active": True},
            ],
            "portal_overrides": [],
            "portal_write_log": [],
        }
        self._seq = 0

    def select(self, table, *, columns="*", params=None, limit=1000):
        rows = self.tables.get(table, [])
        for k, v in (params or {}).items():
            if k == "order":
                continue
            if isinstance(v, str) and v.startswith("eq."):
                # PostgREST renders booleans lowercase ("eq.true"); Python's
                # str(True) is "True". Compare case-folded so the double matches.
                want = v[3:].casefold()
                rows = [r for r in rows if str(r.get(k)).casefold() == want]
        return [dict(r) for r in rows][:limit]

    def insert(self, table, payload):
        self._seq += 1
        row = {"id": f"ov-{self._seq}", **payload}
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def update(self, table, record_id, payload):
        for row in self.tables.get(table, []):
            if row.get("id") == record_id:
                row.update(payload)
                return dict(row)
        raise AssertionError("missing row")


LEDGER_ROW = [{"id": "L1", "policy_number": "P1", "gross_premium": 0,
               "expected_commission": 535.65, "reconciliation_status": "pending"}]


def test_override_sets_a_correction(client):
    supa = OverrideSupa(list(LEDGER_ROW))
    with patch("hermes.routers.deps.get_supa", return_value=supa):
        r = client.post("/api/commissions/L1/override", json={
            "field_name": "gross_premium", "value": 12000,
            "approved_by": "lamar@risksolutionsgroup.net", "reason": "AMS blank",
        })
    assert r.status_code == 200, r.text
    ov = supa.tables["portal_overrides"][0]
    assert ov["entity_key"] == "P1"          # keyed by policy_number, not row id
    assert ov["override_value"] == 12000
    assert ov["original_value"] == 0         # the SOURCE value, for reconcile
    assert supa.tables["portal_write_log"]


def test_override_rejects_a_non_overridable_field(client):
    supa = OverrideSupa(list(LEDGER_ROW))
    with patch("hermes.routers.deps.get_supa", return_value=supa):
        r = client.post("/api/commissions/L1/override", json={
            "field_name": "reconciliation_status", "value": "reconciled",
            "approved_by": "lamar@risksolutionsgroup.net",
        })
    assert r.status_code == 400
    assert "overridable" in r.json()["detail"]


def test_override_rejects_an_unknown_approver(client):
    supa = OverrideSupa(list(LEDGER_ROW))
    with patch("hermes.routers.deps.get_supa", return_value=supa):
        r = client.post("/api/commissions/L1/override", json={
            "field_name": "gross_premium", "value": 1,
            "approved_by": "lamar@risk-solutionsgroup.com",   # .com
        })
    assert r.status_code == 400
    assert "agency_crm_users" in r.json()["detail"]


def test_override_on_a_missing_row_is_404(client):
    supa = OverrideSupa([])
    with patch("hermes.routers.deps.get_supa", return_value=supa):
        r = client.post("/api/commissions/nope/override", json={
            "field_name": "gross_premium", "value": 1,
            "approved_by": "lamar@risksolutionsgroup.net",
        })
    assert r.status_code == 404


def test_override_needs_a_policy_number_to_key_on(client):
    supa = OverrideSupa([{"id": "L1", "policy_number": None, "gross_premium": 0}])
    with patch("hermes.routers.deps.get_supa", return_value=supa):
        r = client.post("/api/commissions/L1/override", json={
            "field_name": "gross_premium", "value": 1,
            "approved_by": "lamar@risksolutionsgroup.net",
        })
    assert r.status_code == 400
    assert "policy_number" in r.json()["detail"]


def test_overridden_value_shows_on_the_read_with_its_original(client):
    supa = OverrideSupa(list(LEDGER_ROW))
    with patch("hermes.routers.deps.get_supa", return_value=supa):
        client.post("/api/commissions/L1/override", json={
            "field_name": "gross_premium", "value": 12000,
            "approved_by": "lamar@risksolutionsgroup.net",
        })
        with patch("hermes.ams.book.select_policies", return_value=[]):
            body = client.get("/api/commissions?status=all").json()
    row = body["commissions"][0]
    assert row["gross_premium"] == 12000
    assert row["_overridden"] == {"gross_premium": 0}
    assert body["active_overrides"] == 1


def test_withdraw_requires_a_valid_approver(client):
    supa = OverrideSupa(list(LEDGER_ROW))
    with patch("hermes.routers.deps.get_supa", return_value=supa):
        client.post("/api/commissions/L1/override", json={
            "field_name": "gross_premium", "value": 1,
            "approved_by": "lamar@risksolutionsgroup.net",
        })
        oid = supa.tables["portal_overrides"][0]["id"]
        bad = client.delete(f"/api/commissions/overrides/{oid}?approved_by=x@y.com")
        good = client.delete(
            f"/api/commissions/overrides/{oid}"
            "?approved_by=lamar@risksolutionsgroup.net"
        )
    assert bad.status_code == 400
    assert good.status_code == 200
    assert supa.tables["portal_overrides"][0]["status"] == "retired"



# --- analytics endpoint (#236) ----------------------------------------------

ANALYTICS_LEDGER = [
    {"policy_number": "A", "carrier_name": "Progressive", "lob": "Auto",
     "gross_premium": 1000, "expected_commission": 150, "actual_commission": 150,
     "delta": 0, "reconciliation_status": "reconciled", "statement_date": "2026-07-08"},
    {"policy_number": "B", "carrier_name": "Progressive", "lob": "Auto",
     "gross_premium": 2000, "expected_commission": 300, "actual_commission": 250,
     "delta": -50, "reconciliation_status": "underpaid", "statement_date": "2026-07-08"},
    {"policy_number": "C", "carrier_name": "Next", "lob": "Home",
     "gross_premium": 5000, "expected_commission": 750, "actual_commission": None,
     "delta": None, "reconciliation_status": "pending", "statement_date": None},
]


def test_analytics_endpoint_returns_per_carrier_and_per_lob(client):
    with patch("hermes.routers.deps.get_supa", return_value=FakeSupa(ANALYTICS_LEDGER)):
        r = client.get("/api/commissions/analytics")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"by_carrier", "by_lob", "totals"}
    carriers = {b["key"]: b for b in body["by_carrier"]}
    assert carriers["Progressive"]["policies"] == 2
    assert carriers["Progressive"]["expected_commission"] == 450
    assert carriers["Next"]["actual_commission"] == 0  # None coerced to 0
    lobs = {b["key"]: b for b in body["by_lob"]}
    assert lobs["Auto"]["policies"] == 2 and lobs["Home"]["policies"] == 1
    assert body["totals"]["ledger_rows"] == 3
    assert body["totals"]["expected_commission"] == 1200
    assert body["totals"]["counts_by_status"]["pending"] == 1


def test_analytics_endpoint_is_honest_on_empty_ledger(client):
    with patch("hermes.routers.deps.get_supa", return_value=FakeSupa([])):
        r = client.get("/api/commissions/analytics")
    assert r.status_code == 200
    body = r.json()
    assert body["by_carrier"] == [] and body["by_lob"] == []
    assert body["totals"]["ledger_rows"] == 0
