"""Tests for the Command Center Renewals Cockpit (Phase 1)."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app
from hermes.operations.renewal_tracker import summarize_renewals


@pytest.fixture(autouse=True)
def _reset_singletons():
    import hermes.api as api_mod

    api_mod._supa = None
    yield
    api_mod._supa = None


@pytest.fixture
def client():
    return TestClient(app)


def _rows(today: date) -> list[dict]:
    return [
        # past-due
        {"id": "1", "client_name": "Lapsed Co", "policy_number": "P-1",
         "expiration_date": (today - timedelta(days=10)).isoformat(), "premium_current": 5000,
         "risk_status": "SAFE"},
        # ≤7 days
        {"id": "2", "client_name": "Urgent LLC", "policy_number": "P-2",
         "expiration_date": (today + timedelta(days=3)).isoformat(), "premium_current": 12000,
         "premium_renewal": 13000, "increase_percentage": 8.3, "risk_status": "AT_RISK"},
        # ≤30 days
        {"id": "3", "client_name": "Soon Inc", "policy_number": "P-3",
         "expiration_date": (today + timedelta(days=20)).isoformat(), "premium_current": 8000,
         "risk_status": "SAFE"},
        # ≤90 days
        {"id": "4", "client_name": "Later Corp", "policy_number": "P-4",
         "expiration_date": (today + timedelta(days=80)).isoformat(), "premium_current": 3000},
        # >90 days (excluded from upcoming)
        {"id": "5", "client_name": "Distant Co", "policy_number": "P-5",
         "expiration_date": (today + timedelta(days=200)).isoformat(), "premium_current": 9000},
        # no date
        {"id": "6", "client_name": "Undated Co", "policy_number": "P-6",
         "expiration_date": None, "premium_current": 1000},
    ]


class TestSummarizeRenewals:
    def test_bucketing_counts_and_premiums(self) -> None:
        today = date(2026, 6, 2)
        out = summarize_renewals(_rows(today), today=today)

        assert out["total"] == 6
        assert out["past_due_count"] == 1
        b = out["buckets"]
        assert b["past_due"]["count"] == 1 and b["past_due"]["premium_current"] == 5000
        assert b["le7"]["count"] == 1
        assert b["le30"]["count"] == 1
        assert b["le90"]["count"] == 1
        assert b["gt90"]["count"] == 1
        assert b["no_date"]["count"] == 1

    def test_upcoming_excludes_past_due_distant_and_undated(self) -> None:
        today = date(2026, 6, 2)
        out = summarize_renewals(_rows(today), today=today)
        names = [r["client_name"] for r in out["upcoming"]]
        assert names == ["Urgent LLC", "Soon Inc", "Later Corp"]  # sorted by days_until
        assert out["upcoming_count"] == 3

    def test_upcoming_carries_renewal_fields(self) -> None:
        today = date(2026, 6, 2)
        out = summarize_renewals(_rows(today), today=today)
        urgent = out["upcoming"][0]
        assert urgent["days_until"] == 3
        assert urgent["increase_percentage"] == 8.3
        assert urgent["risk_status"] == "AT_RISK"

    def test_handles_empty(self) -> None:
        out = summarize_renewals([], today=date(2026, 6, 2))
        assert out["total"] == 0 and out["upcoming"] == [] and out["past_due_count"] == 0


class TestRenewalsEndpoint:
    @patch("hermes.api._get_supa")
    def test_endpoint_returns_summary(self, mock_get_supa, client) -> None:
        today = date.today()
        mock_supa = MagicMock()
        mock_supa.select.return_value = _rows(today)
        mock_get_supa.return_value = mock_supa

        resp = client.get("/api/command-center/renewals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 6
        assert data["past_due_count"] == 1
        assert {"as_of", "buckets", "upcoming", "upcoming_count"} <= data.keys()
        # read came from the right table
        assert mock_supa.select.call_args[0][0] == "project_85_renewals"
