"""Tests for the Walker on-demand renewal API (hermes/walker)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app
from hermes.walker.router import reset_walker


@pytest.fixture(autouse=True)
def _reset():
    reset_walker()
    yield
    reset_walker()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_walker():
    """A fully mocked WalkerService injected into the router."""
    from hermes.walker import router as router_mod
    walker = MagicMock()
    router_mod._walker = walker
    return walker


class TestAuth:
    def test_queue_requires_token_when_set(self, client, mock_walker, monkeypatch):
        monkeypatch.setenv("WALKER_API_TOKEN", "secret123")
        resp = client.get("/walker/queue")
        assert resp.status_code == 401

    def test_queue_passes_with_token(self, client, mock_walker, monkeypatch):
        monkeypatch.setenv("WALKER_API_TOKEN", "secret123")
        mock_walker.get_queue.return_value = {"data_as_of": "now", "count": 0, "items": []}
        resp = client.get("/walker/queue", headers={"Authorization": "Bearer secret123"})
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_no_token_allows_access(self, client, mock_walker, monkeypatch):
        monkeypatch.delenv("WALKER_API_TOKEN", raising=False)
        monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
        mock_walker.get_queue.return_value = {"data_as_of": "now", "count": 0, "items": []}
        resp = client.get("/walker/queue")
        assert resp.status_code == 200


class TestReads:
    def test_queue(self, client, mock_walker):
        mock_walker.get_queue.return_value = {
            "data_as_of": "2026-07-13",
            "days_window": 60,
            "count": 2,
            "items": [
                {"client": "Rebecca Perez", "policy_number": "971170598", "risk": "CRITICAL"},
                {"client": "Gray", "policy_number": "NC-100", "risk": "AT_RISK"},
            ],
        }
        resp = client.get("/walker/queue?days=60")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["items"][0]["client"] == "Rebecca Perez"
        assert "data_as_of" in data

    def test_queue_default_days(self, client, mock_walker):
        mock_walker.get_queue.return_value = {"data_as_of": "x", "count": 0, "items": []}
        client.get("/walker/queue")
        mock_walker.get_queue.assert_called_once_with(days=60)

    def test_renewal_detail(self, client, mock_walker):
        mock_walker.get_renewal_detail.return_value = {
            "data_as_of": "live",
            "renewal_id": "r-1",
            "client": "Nubian Clean",
            "nowcerts": {"insured": {}, "policies": [], "account_total_premium": 12000.0},
            "opportunity": {"id": "opp-1"},
        }
        resp = client.get("/walker/renewal/r-1")
        assert resp.status_code == 200
        assert resp.json()["client"] == "Nubian Clean"
        assert "data_as_of" in resp.json()

    def test_renewal_detail_404(self, client, mock_walker):
        mock_walker.get_renewal_detail.side_effect = ValueError("not found")
        resp = client.get("/walker/renewal/bad-id")
        assert resp.status_code == 404

    def test_search(self, client, mock_walker):
        mock_walker.search.return_value = {
            "data_as_of": "x",
            "query": "Perez",
            "count": 1,
            "results": [{"client": "Rebecca Perez", "policy_number": "971170598"}],
        }
        resp = client.get("/walker/search?q=Perez")
        assert resp.status_code == 200
        assert resp.json()["results"][0]["client"] == "Rebecca Perez"

    def test_search_empty_q(self, client, mock_walker):
        mock_walker.search.return_value = {"data_as_of": "x", "query": "", "count": 0, "results": []}
        resp = client.get("/walker/search")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_quiet_lapse(self, client, mock_walker):
        mock_walker.get_quiet_lapse.return_value = {
            "data_as_of": "x", "count": 3, "items": [],
        }
        resp = client.get("/walker/quiet-lapse")
        assert resp.status_code == 200
        assert resp.json()["count"] == 3

    def test_scoreboard(self, client, mock_walker):
        mock_walker.get_scoreboard.return_value = {
            "data_as_of": "x", "total_renewals": 104,
            "retention_pct": 54.9, "renewed_premium": 200000.0,
            "lost_premium": 185000.0, "active_premium": 380000.0,
        }
        resp = client.get("/walker/scoreboard")
        assert resp.status_code == 200
        assert resp.json()["retention_pct"] == 54.9


class TestWrites:
    def test_post_touch(self, client, mock_walker):
        mock_walker.post_touch.return_value = {"ok": True, "opportunity_id": "opp-1", "touch_logged": "now"}
        resp = client.post("/walker/touch/opp-1", json={"actor": "lamar", "note": "Sent Day-1 email"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_patch_worksheet(self, client, mock_walker):
        mock_walker.patch_worksheet.return_value = {"ok": True, "updated_fields": ["cDay1SentAt"]}
        resp = client.patch("/walker/worksheet/opp-1", json={"fields": {"cDay1SentAt": "2026-07-13"}})
        assert resp.status_code == 200
        assert "cDay1SentAt" in resp.json()["updated_fields"]

    def test_patch_worksheet_empty(self, client, mock_walker):
        mock_walker.patch_worksheet.side_effect = ValueError("No valid fields to update")
        resp = client.patch("/walker/worksheet/opp-1", json={"fields": {}})
        assert resp.status_code == 400

    def test_post_flag(self, client, mock_walker):
        mock_walker.post_flag.return_value = {"ok": True, "flags": ["GL open since 2025-10-04"]}
        resp = client.post("/walker/flag/opp-1", json={"flag": "GL open since 2025-10-04"})
        assert resp.status_code == 200
        assert "GL open since 2025-10-04" in resp.json()["flags"]

    def test_post_flag_empty(self, client, mock_walker):
        mock_walker.post_flag.side_effect = ValueError("flag text required")
        resp = client.post("/walker/flag/opp-1", json={"flag": ""})
        assert resp.status_code == 400

    def test_post_handoff(self, client, mock_walker):
        mock_walker.post_handoff.return_value = {"ok": True, "opportunity_id": "opp-1"}
        resp = client.post("/walker/handoff/opp-1", json={"note": "Gretchen: please pull the declaration"})
        assert resp.status_code == 200

    def test_post_outcome(self, client, mock_walker):
        mock_walker.post_outcome.return_value = {"ok": True, "decision": "renewed", "stage": "Closed"}
        resp = client.post("/walker/outcome/opp-1", json={"decision": "renewed", "stage": "Closed"})
        assert resp.status_code == 200
        assert resp.json()["decision"] == "renewed"


class TestServiceLogic:
    """Test the service layer with mocked clients."""

    def test_get_queue_classifies_at_request_time(self):
        from hermes.walker.service import WalkerService
        supa = MagicMock()
        supa.select.side_effect = [
            [{"id": "1", "policy_number": "P1", "client_name": "Test Co",
              "expiration_date": "2026-08-01", "premium_current": 5000,
              "risk_status": "SAFE", "increase_percentage": None}],
            [],  # commissions
            [{"updated_at": "2026-07-13T08:00:00Z"}],  # freshness stamp
        ]
        svc = WalkerService(supa=supa)
        result = svc.get_queue(days=60)
        assert result["count"] == 1
        assert "data_as_of" in result
        assert result["items"][0]["client"] == "Test Co"

    def test_search_finds_by_name(self):
        from hermes.walker.service import WalkerService
        supa = MagicMock()
        supa.select.side_effect = [
            [],  # by_policy (no match)
            [{"id": "1", "policy_number": "P1", "client_name": "Rebecca Perez",
              "expiration_date": "2026-08-01", "premium_current": 3000,
              "risk_status": "CRITICAL"}],  # by_name
            [{"updated_at": "2026-07-13"}],  # freshness
        ]
        svc = WalkerService(supa=supa)
        result = svc.search("Perez")
        assert result["count"] == 1
        assert result["results"][0]["client"] == "Rebecca Perez"

    def test_scoreboard_calculates_retention(self):
        from hermes.walker.service import WalkerService
        supa = MagicMock()
        supa.select.side_effect = [
            [
                {"normalized_status": "Renewed", "eligibility_state": "excluded", "premium_current": 200000},
                {"normalized_status": "Cancelled", "eligibility_state": "excluded", "premium_current": 100000},
                {"normalized_status": "Active", "eligibility_state": "eligible", "premium_current": 85000},
            ],
            [{"updated_at": "2026-07-13"}],  # freshness
        ]
        svc = WalkerService(supa=supa)
        result = svc.get_scoreboard()
        assert result["retention_pct"] == 66.7
        assert result["renewed_premium"] == 200000.0
        assert result["lost_premium"] == 100000.0

    def test_post_touch_appends_to_log(self):
        from hermes.walker.service import WalkerService
        espo = MagicMock()
        espo.get.return_value = {"id": "opp-1", "cTouchLog": '[{"timestamp": "2026-07-12", "actor": "gretchen", "note": "called"}]', "cLastClientContactDate": "2026-07-12"}
        svc = WalkerService(espo=espo)
        result = svc.post_touch("opp-1", {"actor": "lamar", "note": "sent email"})
        assert result["ok"] is True
        espo.update.assert_called_once()
        call_args = espo.update.call_args
        payload = call_args[0][2]
        assert "cTouchLog" in payload
        assert "cLastClientContactDate" in payload

    def test_post_flag_idempotent(self):
        from hermes.walker.service import WalkerService
        espo = MagicMock()
        espo.get.return_value = {"id": "opp-1", "cComplexityFlags": "GL open since 2025-10-04"}
        svc = WalkerService(espo=espo)
        result = svc.post_flag("opp-1", {"flag": "GL open since 2025-10-04"})
        # Flag already exists -- list should not grow
        assert result["flags"] == ["GL open since 2025-10-04"]
        assert len(result["flags"]) == 1

class TestHandoffsAndStatus:
    def test_handoffs_endpoint(self, client, mock_walker):
        mock_walker.get_handoffs.return_value = {
            "data_as_of": "2026-07-14",
            "count": 2,
            "items": [
                {"id": "opp-1", "client": "Richards Construction", "owner": "Lamar",
                 "handoff_notes": "Needs quote review"},
                {"id": "opp-2", "client": "Gray Trucking", "owner": "Gretchen",
                 "handoff_notes": "Endorsement pending"},
            ],
        }
        resp = client.get("/walker/handoffs")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2
        assert resp.json()["items"][0]["client"] == "Richards Construction"

    def test_handoffs_filtered_by_owner(self, client, mock_walker):
        mock_walker.get_handoffs.return_value = {"data_as_of": "x", "count": 1, "items": []}
        client.get("/walker/handoffs?owner=Lamar")
        mock_walker.get_handoffs.assert_called_once_with(owner="Lamar")

    def test_status_endpoint(self, client, mock_walker):
        mock_walker.get_status.return_value = {
            "data_as_of": "2026-07-14T20:30:00Z (live)",
            "renewal_id": "r-1",
            "client": "Nubian Clean",
            "stage": "Negotiation",
            "owner": "Lamar",
            "days_out": 12,
            "decision": "",
            "flags": [],
            "handoff_notes": None,
            "last_touch": None,
            "last_contact_date": None,
            "day1_sent_at": "2026-07-01",
            "touch_count": 0,
            "nowcerts_policies": 3,
            "next_action": "Day-4 text nudge overdue.",
        }
        resp = client.get("/walker/status/r-1")
        assert resp.status_code == 200
        assert resp.json()["next_action"] == "Day-4 text nudge overdue."
        assert resp.json()["days_out"] == 12

    def test_status_404(self, client, mock_walker):
        mock_walker.get_status.side_effect = ValueError("not found")
        resp = client.get("/walker/status/bad-id")
        assert resp.status_code == 404

    def test_post_flag_escalates_to_lamar(self):
        from hermes.walker.service import WalkerService
        espo = MagicMock()
        espo.get.return_value = {
            "id": "opp-1",
            "cComplexityFlags": "",
            "cRenewalOwner": "Gretchen",
        }
        svc = WalkerService(espo=espo)
        result = svc.post_flag("opp-1", {"flag": "Needs Lamar to quote"})
        assert result["owner_changed_to"] == "Lamar"
        call_args = espo.update.call_args[0][2]
        assert call_args["cRenewalOwner"] == "Lamar"
        assert "cComplexityFlags" in call_args

    def test_post_flag_delegates_to_gretchen(self):
        from hermes.walker.service import WalkerService
        espo = MagicMock()
        espo.get.return_value = {
            "id": "opp-1",
            "cComplexityFlags": "",
            "cRenewalOwner": "Lamar",
        }
        svc = WalkerService(espo=espo)
        result = svc.post_flag("opp-1", {"flag": "Gretchen can handle service item"})
        assert result["owner_changed_to"] == "Gretchen"
        call_args = espo.update.call_args[0][2]
        assert call_args["cRenewalOwner"] == "Gretchen"

    def test_post_flag_no_owner_change_for_neutral_flag(self):
        from hermes.walker.service import WalkerService
        espo = MagicMock()
        espo.get.return_value = {
            "id": "opp-1",
            "cComplexityFlags": "",
            "cRenewalOwner": "Lamar",
        }
        svc = WalkerService(espo=espo)
        result = svc.post_flag("opp-1", {"flag": "GL open since 2025-10-04"})
        assert "owner_changed_to" not in result
        call_args = espo.update.call_args[0][2]
        assert "cRenewalOwner" not in call_args
