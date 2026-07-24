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
    """classify_risk is URGENCY-ONLY now — lifecycle status is ignored (terminal
    states are excluded upstream by the eligibility engine and never reach here)."""

    TODAY = date(2026, 6, 2)

    def _c(self, status, days, **kw):
        exp = (self.TODAY + timedelta(days=days)).isoformat() if days is not None else None
        return classify_risk(policy_status=status, expiration_date=exp, today=self.TODAY, **kw)

    def test_urgency_by_timing(self) -> None:
        assert self._c("Active", -1) == "CRITICAL"      # past x-date
        assert self._c("Active", 20) == "CRITICAL"      # <=30d
        assert self._c("Active", 75) == "AT_RISK"       # <=90d
        assert self._c("Active", 200) == "SAFE"         # >90d
        assert self._c(None, None) == "SAFE"            # no date
        assert self._c("", 200) == "SAFE"

    def test_status_no_longer_drives_result(self) -> None:
        # Any status classifies purely on timing now — never RENEWED/LAPSED.
        for status in ("Renewed", "Expired", "Cancelled", "Pending Cancel", "Renewing"):
            assert self._c(status, 200) == "SAFE"
            assert self._c(status, 10) == "CRITICAL"

    def test_increase_override_when_quote_exists(self) -> None:
        assert self._c("Active", 200, increase_percentage=20.0) == "CRITICAL"
        assert self._c("Active", 200, increase_percentage=8.0) == "AT_RISK"


class TestRefreshRenewals:
    """refresh_renewals re-grades urgency over ELIGIBLE renewal_candidates."""

    def test_refresh_regrades_urgency_on_candidates(self) -> None:
        today = date(2026, 6, 2)
        supa = MagicMock()
        supa.select.return_value = [
            {"id": "1", "policy_number": "P-1", "normalized_status": "Active",
             "renewal_event_date": (today + timedelta(days=10)).isoformat(),
             "premium_current": 1000, "premium_renewal": None, "risk_status": "SAFE"},
            {"id": "2", "policy_number": "P-2", "normalized_status": "Renewing",
             "renewal_event_date": (today + timedelta(days=200)).isoformat(),
             "premium_current": 1000, "premium_renewal": None, "risk_status": "SAFE"},
        ]
        summary = refresh_renewals(supa, dry_run=False, today=today)

        assert summary["total"] == 2
        assert summary["by_risk"]["CRITICAL"] == 1   # P-1 <=30d
        assert summary["by_risk"]["SAFE"] == 1       # P-2 >90d, no change
        assert summary["changed"] == 1               # only P-1 flips
        # renewal_candidates.risk_status updated for the changed row
        assert any(c[0][0] == "renewal_candidates" and c[0][1] == "1" for c in supa.update.call_args_list)

    def test_dry_run_does_not_write(self) -> None:
        today = date(2026, 6, 2)
        supa = MagicMock()
        supa.select.return_value = [
            {"id": "1", "policy_number": "P-1", "normalized_status": "Active",
             "renewal_event_date": (today + timedelta(days=10)).isoformat(),
             "premium_current": 1000, "premium_renewal": None, "risk_status": "SAFE"},
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
    @patch("hermes.core.nl_agent.ask")
    def test_ask_non_renewal_routes_to_agent(self, mock_ask, mock_espo, client) -> None:
        from hermes.core.dispatcher import DispatchResult
        mock_ask.return_value = DispatchResult(True, "We have 554 accounts.")
        resp = client.post("/api/command-center/ask", json={"prompt": "How many accounts do we have?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True and "554" in data["message"]
        # conversational agent, writes previewed only
        assert mock_ask.call_args.kwargs["confirmed"] is False

    def test_ask_empty_prompt(self, client) -> None:
        resp = client.post("/api/command-center/ask", json={"prompt": "  "})
        assert resp.status_code == 400

    @patch("hermes.operations.command_center_qa._llm_answer", return_value=None)
    @patch("hermes.api._get_supa")
    @patch("hermes.api._get_dispatcher")
    def test_ask_renewal_intent_answered_from_data(self, mock_disp, mock_get_supa, _no_llm, client) -> None:
        supa = MagicMock()
        supa.select.return_value = [
            {"id": "1", "policy_number": "Acme | General Liability | 9", "client_name": "Acme",
             "expiration_date": (date.today() + timedelta(days=3)).isoformat(),
             "premium_current": 5000, "risk_status": "CRITICAL"},
        ]
        mock_get_supa.return_value = supa
        resp = client.post("/api/command-center/ask",
                           json={"prompt": "Who renews this week and what should I do about each one?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "command-center"
        assert "Acme" in data["message"] and "renew" in data["message"].lower()
        mock_disp.return_value.dispatch.assert_not_called()  # renewals never hit the name-search route


class TestCommandCenterQA:
    TODAY = date(2026, 6, 2)

    def _rows(self):
        t = self.TODAY
        return [
            {"id": "1", "policy_number": "Acme | General Liability | 9", "client_name": "Acme",
             "expiration_date": (t + timedelta(days=3)).isoformat(), "premium_current": 5000, "risk_status": "CRITICAL"},
            {"id": "2", "policy_number": "Beta | Workers Comp | 8", "client_name": "Beta Inc",
             "expiration_date": (t + timedelta(days=45)).isoformat(), "premium_current": 12000, "risk_status": "AT_RISK"},
            {"id": "3", "policy_number": "Gamma | GL | 7", "client_name": "Gamma",
             "expiration_date": (t + timedelta(days=200)).isoformat(), "premium_current": 9000, "risk_status": "SAFE"},
        ]

    def test_non_matching_prompt_returns_none(self):
        from hermes.operations.command_center_qa import answer_question
        supa = MagicMock()
        assert answer_question(supa, "what's the weather", today=self.TODAY, use_llm=False) is None
        supa.select.assert_not_called()

    def test_this_week_window(self):
        from hermes.operations.command_center_qa import answer_question
        supa = MagicMock(); supa.select.return_value = self._rows()
        ans = answer_question(supa, "Who renews this week?", today=self.TODAY, use_llm=False)
        assert "next 7 days" in ans and "Acme" in ans and "Beta" not in ans

    def test_at_risk_question(self):
        from hermes.operations.command_center_qa import answer_question
        supa = MagicMock(); supa.select.return_value = self._rows()
        ans = answer_question(supa, "Which clients are most at risk of leaving?", today=self.TODAY, use_llm=False)
        # both CRITICAL + AT_RISK in 90d, biggest premium first
        assert "Beta Inc" in ans and "Acme" in ans
        assert ans.index("Beta Inc") < ans.index("Acme")

    def test_renewals_facts_scope(self):
        from hermes.operations.command_center_qa import renewals_facts
        supa = MagicMock(); supa.select.return_value = self._rows()
        upc = renewals_facts(supa, scope="upcoming", within_days=7, today=self.TODAY)
        assert "Acme" in upc and "Beta" not in upc
        risk = renewals_facts(supa, scope="at_risk", today=self.TODAY)
        assert "Beta Inc" in risk and "Gamma" not in risk  # SAFE excluded


class TestFilesStore:
    def test_save_file_rejects_empty(self):
        from hermes.operations.files_store import save_file
        supa = MagicMock()
        with pytest.raises(ValueError):
            save_file(supa, title="x", content="   ")
        supa.insert.assert_not_called()

    def test_save_file_normalises(self):
        from hermes.operations.files_store import save_file
        supa = MagicMock(); supa.insert.side_effect = lambda t, row: {**row, "id": "f1"}
        out = save_file(supa, title="My Note", content="hi", kind="bogus")
        assert out["kind"] == "other" and supa.insert.call_args[0][0] == "hermes_files"

    def test_download_filename_sanitises(self):
        from hermes.operations.files_store import download_filename
        assert download_filename({"title": "Save list: at-risk!", "file_ext": "md"}) == "Save_list_at-risk.md"
        assert download_filename({"title": None}).endswith(".md")


class TestFilesEndpoints:
    @patch("hermes.api._get_supa")
    def test_save_and_list(self, mock_get_supa, client) -> None:
        supa = MagicMock()
        supa.insert.side_effect = lambda t, row: {**row, "id": "f1"}
        supa.select.return_value = [{"id": "f1", "title": "Note", "kind": "note",
                                     "content_type": "text/markdown", "file_ext": "md",
                                     "source": "command-center", "created_by": "hermes",
                                     "created_at": "2026-06-02"}]
        mock_get_supa.return_value = supa
        r = client.post("/api/command-center/files", json={"title": "Note", "content": "hello"})
        assert r.status_code == 200 and r.json()["id"] == "f1"
        r2 = client.get("/api/command-center/files")
        assert r2.status_code == 200 and r2.json()["files"][0]["title"] == "Note"

    @patch("hermes.api._get_supa")
    def test_download_sets_attachment_header(self, mock_get_supa, client) -> None:
        supa = MagicMock()
        supa.select.return_value = [{"id": "f1", "title": "At Risk", "content": "# body",
                                     "content_type": "text/markdown", "file_ext": "md"}]
        mock_get_supa.return_value = supa
        r = client.get("/api/command-center/files/f1/download")
        assert r.status_code == 200
        assert r.text == "# body"
        assert 'attachment; filename="At_Risk.md"' in r.headers["content-disposition"]

    @patch("hermes.api._get_supa")
    def test_download_404(self, mock_get_supa, client) -> None:
        supa = MagicMock(); supa.select.return_value = []
        mock_get_supa.return_value = supa
        assert client.get("/api/command-center/files/missing/download").status_code == 404


class TestNlAgentTools:
    def test_core_tools_registered(self):
        from hermes.core.nl_agent import _EXECUTORS, _TOOLS
        names = {t["function"]["name"] for t in _TOOLS}
        for tool in ("renewals_overview", "web_research", "list_skills"):
            assert tool in names and tool in _EXECUTORS

    def test_renewals_tool_executes(self):
        from hermes.core.nl_agent import _EXECUTORS
        with patch("hermes.integrations.supabase_client.SupabaseClient") as cls:
            inst = MagicMock()
            inst.select.return_value = [
                {"id": "1", "policy_number": "Acme | GL | 9", "client_name": "Acme",
                 "expiration_date": (date.today() + timedelta(days=5)).isoformat(),
                 "premium_current": 5000, "risk_status": "CRITICAL"},
            ]
            cls.return_value = inst
            res = _EXECUTORS["renewals_overview"](None, {"scope": "upcoming", "within_days": 30})
            assert res.ok and "Acme" in res.message

    def test_list_skills_tool(self):
        from hermes.core.nl_agent import _EXECUTORS
        res = _EXECUTORS["list_skills"](None, {})
        assert res.ok and "tools I can run" in res.message


class TestTeamQueue:
    TODAY = date(2026, 6, 2)

    def _body(self):
        t = self.TODAY
        return {"list": [
            {"id": "t1", "name": "Call Centeno about fleet", "status": "Not Started",
             "dateEnd": (t + timedelta(days=2)).isoformat() + " 17:00:00", "priority": "High",
             "assignedUserName": "Gretchen", "parentName": "Centeno Logistics", "parentType": "Account"},
            {"id": "t2", "name": "Upload loss runs", "status": "Started",
             "dateEnd": (t - timedelta(days=1)).isoformat() + " 17:00:00", "priority": "Normal",
             "assignedUserName": "Gretchen", "parentName": None, "parentType": None},
            {"id": "t3", "name": "No due date task", "status": "Not Started",
             "dateEnd": None, "priority": "Low", "assignedUserName": "Lamar"},
        ]}

    def test_list_open_tasks_shapes_and_sorts(self):
        from hermes.operations.team_queue import list_open_tasks
        client = MagicMock(); client.get.return_value = self._body()
        tasks = list_open_tasks(client, today=self.TODAY)
        # overdue first, no-due-date last
        assert [t["id"] for t in tasks] == ["t2", "t1", "t3"]
        assert tasks[0]["due_label"] == "overdue 1d"
        assert tasks[1]["due_label"] == "due in 2d"
        assert tasks[2]["due_label"] == "no due date"
        # excludes closed statuses via where-clause
        where = client.get.call_args.kwargs["params"]["where"][0]
        assert where["type"] == "notIn" and "Completed" in where["value"]

    def test_group_by_assignee(self):
        from hermes.operations.team_queue import group_by_assignee, list_open_tasks
        client = MagicMock(); client.get.return_value = self._body()
        grouped = group_by_assignee(list_open_tasks(client, today=self.TODAY))
        assert set(grouped) == {"Gretchen", "Lamar"} and len(grouped["Gretchen"]) == 2

    def test_complete_task_writes_status(self):
        from hermes.operations.team_queue import complete_task
        client = MagicMock(); client.update.return_value = {"id": "t1", "status": "Completed"}
        complete_task(client, "t1")
        client.update.assert_called_once_with("Task", "t1", {"status": "Completed"})


class TestTeamQueueEndpoints:
    @patch("hermes.api._get_supa")
    def test_tasks_endpoint(self, mock_get_supa, client) -> None:
        """Reads agency_crm_tasks, not EspoCRM — Espo was decommissioned
        2026-07-23, and a dead-host call on a polled endpoint hung the pool."""
        supa = MagicMock()
        supa.select.return_value = [
            {"id": "t1", "title": "Call client", "status": "open", "priority": "high",
             "due_at": (date.today() + timedelta(days=1)).isoformat(),
             "assigned_to_email": "gretchen@risksolutionsgroup.net", "case_id": "c1"},
        ]
        mock_get_supa.return_value = supa
        r = client.get("/api/command-center/tasks")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1 and "Gretchen" in data["by_assignee"]
        assert data["source"] == "agency_crm_tasks"
        assert supa.select.call_args.args[0] == "agency_crm_tasks"

    @patch("hermes.api._get_supa")
    def test_tasks_endpoint_maps_unknown_assignee(self, mock_get_supa, client) -> None:
        supa = MagicMock()
        supa.select.return_value = [
            {"id": "t2", "title": "Review", "assigned_to_email": "dana@example.com"},
            {"id": "t3", "title": "Orphan", "assigned_to_email": None},
        ]
        mock_get_supa.return_value = supa
        data = client.get("/api/command-center/tasks").json()
        assert set(data["by_assignee"]) == {"Dana", "Unassigned"}

    @patch("hermes.api._get_espo")
    def test_complete_endpoint(self, mock_espo, client) -> None:
        c = MagicMock(); c.update.return_value = {"id": "t1", "status": "Completed"}
        mock_espo.return_value = c
        r = client.post("/api/command-center/tasks/t1/complete")
        assert r.status_code == 200 and r.json()["ok"] is True
        c.update.assert_called_once_with("Task", "t1", {"status": "Completed"})


class TestSkillsCatalog:
    def test_catalog_lists_tools_and_skills(self):
        from hermes.operations.skills_catalog import catalog
        cat = catalog()
        names = {t["name"] for t in cat["runtime_tools"]}
        assert {"renewals_overview", "web_research", "list_skills"} <= names
        assert cat["counts"]["runtime_tools"] >= 8
        # domain skills are read from .claude/skills (present in repo)
        assert cat["counts"]["domain_skills"] >= 1

    def test_skills_endpoint(self, client):
        resp = client.get("/api/command-center/skills")
        assert resp.status_code == 200
        body = resp.json()
        assert "runtime_tools" in body and "domain_skills" in body


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
