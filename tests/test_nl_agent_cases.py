"""Cases Desk tools on the NL agent.

These read over HTTP from googrlc/rsg-hermes-cases rather than querying
v_case_progress, so what is worth pinning is that the desk asks the service the
right questions and never recomputes its arithmetic locally.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from hermes.agent import nl_agent


CASES = [
    {"id": "c1", "case_number": "SER-1", "title": "Wix billing",
     "insured_name": "Risk Solutions Group", "status": "open"},
    {"id": "c2", "case_number": "SER-2", "title": "Loss runs",
     "insured_name": "Acme Roofing", "status": "open"},
]

PROGRESS = {
    "c1": {"case_id": "c1", "tasks_total": 3, "tasks_done": 3,
           "required_blocking": 0, "can_close": True},
    "c2": {"case_id": "c2", "tasks_total": 4, "tasks_done": 1,
           "required_blocking": 2, "can_close": False},
}


def fake_get(path, params=None):
    if path == "/api/cases":
        return {"cases": CASES, "count": len(CASES)}
    if path == "/api/cases/blocked":
        return {"blocked_cases": [CASES[1]], "count": 1}
    if path.endswith("/progress"):
        return PROGRESS[path.split("/")[3]]
    if path == "/api/tasks":
        return {"tasks": [
            {"title": "Chase carrier", "status": "not_started", "is_required": True,
             "assigned_to_email": "lamar@rsg.net", "due_at": "2026-08-09T00:00:00Z",
             "sort_order": 2},
            {"title": "Open the case", "status": "completed", "is_required": False,
             "sort_order": 1},
        ]}
    raise AssertionError("unexpected path " + path)


class ListCasesTests(unittest.TestCase):
    def test_it_reports_progress_from_the_service(self) -> None:
        with patch.object(nl_agent, "_cases_get", side_effect=fake_get):
            r = nl_agent._exec_list_cases({})
        self.assertTrue(r.ok)
        self.assertIn("SER-1", r.message)
        self.assertIn("3/3 tasks", r.message)
        self.assertIn("ready to close", r.message)
        self.assertIn("2 required task(s) outstanding", r.message)

    def test_blocked_only_asks_the_service_for_its_own_blocked_set(self) -> None:
        seen = []

        def spy(path, params=None):
            seen.append(path)
            return fake_get(path, params)

        with patch.object(nl_agent, "_cases_get", side_effect=spy):
            r = nl_agent._exec_list_cases({"blocked_only": True})
        self.assertTrue(r.ok)
        self.assertIn("/api/cases/blocked", seen)
        self.assertNotIn("/api/cases", seen)   # not the full list, then filtered here
        self.assertIn("SER-2", r.message)
        self.assertNotIn("SER-1", r.message)

    def test_closable_only_keeps_just_the_closable(self) -> None:
        with patch.object(nl_agent, "_cases_get", side_effect=fake_get):
            r = nl_agent._exec_list_cases({"closable_only": True})
        self.assertTrue(r.ok)
        self.assertIn("SER-1", r.message)
        self.assertNotIn("SER-2", r.message)

    def test_insured_filters_locally_since_the_service_has_no_such_param(self) -> None:
        with patch.object(nl_agent, "_cases_get", side_effect=fake_get):
            r = nl_agent._exec_list_cases({"insured": "acme"})
        self.assertTrue(r.ok)
        self.assertIn("SER-2", r.message)
        self.assertNotIn("SER-1", r.message)

    def test_an_unreachable_service_says_so_rather_than_answering_emptily(self) -> None:
        with patch.object(nl_agent, "_cases_get", side_effect=OSError("connection refused")):
            r = nl_agent._exec_list_cases({})
        self.assertFalse(r.ok)
        self.assertIn("Case lookup failed", r.message)

    def test_one_unreadable_case_does_not_blank_the_whole_list(self) -> None:
        def flaky(path, params=None):
            if path == "/api/cases/c2/progress":
                raise OSError("boom")
            return fake_get(path, params)

        with patch.object(nl_agent, "_cases_get", side_effect=flaky):
            r = nl_agent._exec_list_cases({})
        self.assertTrue(r.ok)
        self.assertIn("SER-1", r.message)
        self.assertIn("SER-2", r.message)


class CaseProgressTests(unittest.TestCase):
    def test_it_matches_on_case_number_and_lists_the_checklist(self) -> None:
        with patch.object(nl_agent, "_cases_get", side_effect=fake_get):
            r = nl_agent._exec_case_progress({"case": "SER-2"})
        self.assertTrue(r.ok)
        self.assertIn("SER-2", r.message)
        self.assertIn("BLOCKED: 2 required", r.message)
        # sort_order drives the checklist, so the opened step comes first
        self.assertLess(r.message.index("Open the case"), r.message.index("Chase carrier"))
        self.assertIn("[x] Open the case", r.message)
        self.assertIn("(required)", r.message)

    def test_it_falls_back_to_the_insured_name(self) -> None:
        with patch.object(nl_agent, "_cases_get", side_effect=fake_get):
            r = nl_agent._exec_case_progress({"case": "acme"})
        self.assertTrue(r.ok)
        self.assertIn("SER-2", r.message)

    def test_an_ambiguous_reference_asks_rather_than_guessing(self) -> None:
        with patch.object(nl_agent, "_cases_get", side_effect=fake_get):
            r = nl_agent._exec_case_progress({"case": "SER"})
        self.assertTrue(r.ok)
        self.assertIn("Several cases match", r.message)

    def test_no_reference_is_refused(self) -> None:
        r = nl_agent._exec_case_progress({"case": "  "})
        self.assertFalse(r.ok)


class DeskWiringTests(unittest.TestCase):
    def test_the_cases_desk_can_actually_read_the_queue(self) -> None:
        self.assertIn("list_cases", nl_agent._HUB_TOOLS["cases"])
        self.assertIn("case_progress", nl_agent._HUB_TOOLS["cases"])

    def test_both_tools_are_registered(self) -> None:
        for name in ("list_cases", "case_progress"):
            self.assertIn(name, nl_agent._EXECUTORS)
            self.assertTrue(any(t["function"]["name"] == name for t in nl_agent._TOOLS))

    def test_finance_is_an_alias_not_a_rename(self) -> None:
        self.assertEqual(nl_agent._HUB_TOOLS["finance"], nl_agent._HUB_TOOLS["commissions"])
        self.assertEqual(nl_agent._HUB_PERSONA["finance"], "commissions")

    def test_every_desk_in_the_roster_is_keyed_by_its_persona(self) -> None:
        """A roster keyed by hub alias tells a desk it is somebody else."""
        for persona in nl_agent._HUB_PERSONA.values():
            self.assertIn(persona, nl_agent._DESKS, persona + " missing from the roster")

    def test_a_desk_is_not_offered_a_referral_to_itself(self) -> None:
        block = nl_agent._desk_roster_block("cases")
        self.assertIn("You are the **cases desk**", block)
        self.assertNotIn("- **cases desk**", block)
        self.assertIn("- **renewals desk**", block)


if __name__ == "__main__":
    unittest.main()
