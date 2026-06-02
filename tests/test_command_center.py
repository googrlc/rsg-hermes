"""Tests for the Command Center Renewals Cockpit (Phase 1)."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app
from hermes.operations.renewal_classifier import classify_risk, refresh_renewals
from hermes.operations.renewal_tracker import summarize_renewals
from hermes.operations.save_list import (
    build_outreach_draft,
    create_save_list,
    parse_lob,
    select_save_list,
)


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

    def test_by_risk_tally(self) -> None:
        today = date(2026, 6, 2)
        rows = [
            {"risk_status": "LAPSED", "premium_current": 1000,
             "expiration_date": (today - timedelta(days=5)).isoformat()},
            {"risk_status": "CRITICAL", "premium_current": 2000,
             "expiration_date": (today + timedelta(days=10)).isoformat()},
            {"risk_status": "SAFE", "premium_current": 500,
             "expiration_date": (today + timedelta(days=200)).isoformat()},
        ]
        out = summarize_renewals(rows, today=today)
        assert out["by_risk"]["LAPSED"]["count"] == 1
        assert out["by_risk"]["CRITICAL"]["premium_current"] == 2000
        assert out["by_risk"]["RENEWED"]["count"] == 0


class TestClassifyRisk:
    TODAY = date(2026, 6, 2)

    def _c(self, status, days, **kw):
        exp = (self.TODAY + timedelta(days=days)).isoformat() if days is not None else None
        return classify_risk(policy_status=status, expiration_date=exp, today=self.TODAY, **kw)

    def test_terminal_states_from_policy_status(self) -> None:
        assert self._c("Renewed", -10) == "RENEWED"
        assert self._c("Expired", -10) == "LAPSED"
        assert self._c("Cancelled", -10) == "LAPSED"
        assert self._c("Flat Cancel", 30) == "LAPSED"
        assert self._c("Non-Renewed", 30) == "LAPSED"

    def test_pending_cancel_and_in_renewal(self) -> None:
        assert self._c("Pending Cancel", 100) == "CRITICAL"
        assert self._c("Up for Renewal", -5) == "CRITICAL"
        assert self._c("Up for Renewal", 20) == "CRITICAL"
        assert self._c("Renewing", 80) == "AT_RISK"

    def test_active_and_unknown_by_timing(self) -> None:
        assert self._c("Active", -1) == "CRITICAL"      # past x-date, not renewed
        assert self._c("Active", 20) == "CRITICAL"      # <=30d
        assert self._c("Active", 75) == "AT_RISK"       # <=90d
        assert self._c("Active", 200) == "SAFE"         # >90d
        assert self._c(None, None) == "SAFE"            # no status, no date
        assert self._c("", 200) == "SAFE"

    def test_increase_override_when_quote_exists(self) -> None:
        assert self._c("Active", 200, increase_percentage=20.0) == "CRITICAL"
        assert self._c("Active", 200, increase_percentage=8.0) == "AT_RISK"
        # terminal status still wins over increase
        assert self._c("Renewed", 200, increase_percentage=20.0) == "RENEWED"


class TestRefreshRenewals:
    def test_refresh_writes_changed_rows(self) -> None:
        today = date(2026, 6, 2)
        supa = MagicMock()
        supa.select.side_effect = [
            # project_85_renewals
            [
                {"id": "1", "policy_number": "P-1", "expiration_date": (today - timedelta(days=5)).isoformat(),
                 "premium_current": 1000, "risk_status": "SAFE"},
                {"id": "2", "policy_number": "P-2", "expiration_date": (today + timedelta(days=10)).isoformat(),
                 "premium_current": None, "risk_status": "SAFE"},
            ],
            # crm_commissions
            [
                {"policy_number": "P-1", "policy_status": "Expired", "premium": 1000,
                 "expiration_date": (today - timedelta(days=5)).isoformat()},
                {"policy_number": "P-2", "policy_status": "Active", "premium": 2500,
                 "expiration_date": (today + timedelta(days=10)).isoformat()},
            ],
        ]
        summary = refresh_renewals(supa, dry_run=False, today=today)

        assert summary["total"] == 2
        assert summary["matched_commissions"] == 2
        assert summary["by_risk"]["LAPSED"] == 1      # P-1 Expired
        assert summary["by_risk"]["CRITICAL"] == 1    # P-2 Active, <=30d
        assert summary["changed"] == 2
        assert supa.update.call_count == 2
        # P-2 had no premium_current → backfilled from commission
        p2_update = [c for c in supa.update.call_args_list if c[0][1] == "2"][0][0][2]
        assert p2_update["premium_current"] == 2500
        assert p2_update["risk_status"] == "CRITICAL"

    def test_dry_run_does_not_write(self) -> None:
        today = date(2026, 6, 2)
        supa = MagicMock()
        supa.select.side_effect = [
            [{"id": "1", "policy_number": "P-1", "expiration_date": (today - timedelta(days=5)).isoformat(),
              "premium_current": 1000, "risk_status": "SAFE"}],
            [{"policy_number": "P-1", "policy_status": "Expired", "premium": 1000,
              "expiration_date": (today - timedelta(days=5)).isoformat()}],
        ]
        summary = refresh_renewals(supa, dry_run=True, today=today)
        assert summary["dry_run"] is True
        assert summary["changed"] == 1
        supa.update.assert_not_called()


class TestSaveList:
    TODAY = date(2026, 6, 2)

    def _renewals(self):
        t = self.TODAY
        return [
            {"id": "1", "policy_number": "Big Co | Commercial Auto | 123", "client_name": "Big Co",
             "expiration_date": (t + timedelta(days=20)).isoformat(), "premium_current": 30000, "risk_status": "CRITICAL"},
            {"id": "2", "policy_number": "Mid Co | Workers Comp | 456", "client_name": "Mid Co Inc",
             "expiration_date": (t + timedelta(days=50)).isoformat(), "premium_current": 12000, "risk_status": "AT_RISK"},
            {"id": "3", "policy_number": "Safe Co | GL | 789", "client_name": "Safe Co",
             "expiration_date": (t + timedelta(days=40)).isoformat(), "premium_current": 99000, "risk_status": "SAFE"},
            {"id": "4", "policy_number": "Far Co | GL | 000", "client_name": "Far Co",
             "expiration_date": (t + timedelta(days=120)).isoformat(), "premium_current": 50000, "risk_status": "CRITICAL"},
        ]

    def test_parse_lob(self) -> None:
        assert parse_lob("Big Co | Commercial Auto | 123") == "Commercial Auto"
        assert parse_lob("nopipes") is None
        assert parse_lob(None) is None

    def test_select_filters_and_sorts(self) -> None:
        sel = select_save_list(self._renewals(), today=self.TODAY, limit=10, within_days=60)
        # Safe (excluded), Far (>60d excluded) → only Big + Mid, sorted by premium desc
        assert [r["client_name"] for r in sel] == ["Big Co", "Mid Co Inc"]
        assert sel[0]["days_until"] == 20

    def test_select_respects_limit(self) -> None:
        sel = select_save_list(self._renewals(), today=self.TODAY, limit=1, within_days=60)
        assert len(sel) == 1 and sel[0]["client_name"] == "Big Co"

    def test_build_draft_content(self) -> None:
        r = {**self._renewals()[0], "days_until": 20}
        d = build_outreach_draft(r, today=self.TODAY)
        assert d["status"] == "DRAFT"
        assert d["line_of_business"] == "Commercial Auto"
        assert "Big" in d["body"] and "renews" in d["body"]
        assert d["channel"] == "email"

    def test_create_save_list_stages_drafts(self) -> None:
        supa = MagicMock()
        supa.select.return_value = self._renewals()
        supa.insert.side_effect = lambda table, row: {**row, "id": "draft-" + row["policy_number"][:3]}
        out = create_save_list(supa, limit=10, within_days=60, today=self.TODAY, batch_id="batch-1")
        assert out["created"] == 2
        assert out["batch_id"] == "batch-1"
        assert supa.insert.call_count == 2
        assert all(c[0][0] == "renewal_outreach_drafts" for c in supa.insert.call_args_list)
        assert all(d["status"] == "DRAFT" for d in out["drafts"])

    def test_create_save_list_empty(self) -> None:
        supa = MagicMock()
        supa.select.return_value = [self._renewals()[2]]  # only the SAFE one
        out = create_save_list(supa, today=self.TODAY)
        assert out["created"] == 0 and out["batch_id"] is None
        supa.insert.assert_not_called()


class TestPhase2Endpoints:
    @patch("hermes.api._get_supa")
    def test_retention_endpoint(self, mock_get_supa, client) -> None:
        supa = MagicMock()
        supa.select.return_value = [{"retention_rate": 54.92, "snapshot_date": "2026-03-31",
                                     "active_premium": 385000, "client_count": 81, "policy_count": 104}]
        mock_get_supa.return_value = supa
        resp = client.get("/api/command-center/retention")
        assert resp.status_code == 200
        data = resp.json()
        assert data["retention_rate"] == 54.92 and data["benchmark"] == 84.0

    @patch("hermes.api._get_supa")
    def test_build_save_list_endpoint(self, mock_get_supa, client) -> None:
        supa = MagicMock()
        supa.select.return_value = [
            {"id": "1", "policy_number": "Big Co | Commercial Auto | 123", "client_name": "Big Co",
             "expiration_date": (date.today() + timedelta(days=20)).isoformat(),
             "premium_current": 30000, "risk_status": "CRITICAL"},
        ]
        supa.insert.side_effect = lambda table, row: {**row, "id": "d1"}
        mock_get_supa.return_value = supa
        resp = client.post("/api/command-center/save-list", json={"limit": 5, "within_days": 60})
        assert resp.status_code == 200
        assert resp.json()["created"] == 1


class TestAskHermes:
    @patch("hermes.api._get_espo")
    @patch("hermes.api._get_dispatcher")
    def test_ask_routes_to_dispatcher(self, mock_disp, mock_espo, client) -> None:
        from hermes.core.dispatcher import DispatchResult
        mock_disp.return_value.dispatch.return_value = DispatchResult(True, "3D Pumps renews June 14.")
        resp = client.post("/api/command-center/ask", json={"prompt": "When does 3D Pumps renew?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True and "3D Pumps" in data["message"]
        # confirmed=False enforced (read-only posture)
        assert mock_disp.return_value.dispatch.call_args.kwargs["confirmed"] is False

    def test_ask_blocks_write_intent(self, client) -> None:
        resp = client.post("/api/command-center/ask", json={"prompt": "create a new lead for Acme Corp"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False and data["requires_confirmation"] is True

    def test_ask_empty_prompt(self, client) -> None:
        resp = client.post("/api/command-center/ask", json={"prompt": "  "})
        assert resp.status_code == 400


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
