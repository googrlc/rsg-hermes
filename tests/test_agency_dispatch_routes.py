"""Tests for the agency-memory dispatcher routes.

Verifies that:
  - "What is X's EIN?" routes to agency_fact (NOT to lookup.handle)
  - "Stage intake: <text>" routes to agency_intake
  - Broad questions without a recognized fact label still go to lookup
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from hermes.core.dispatcher import Dispatcher


def _make_dispatcher() -> Dispatcher:
    """Build a Dispatcher without initializing the live Supabase client."""
    d = Dispatcher.__new__(Dispatcher)
    d.use_openai = False
    d.supa = None
    d._slack_ctx = {}
    Dispatcher.__init__(d, use_openai=False)
    d.supa = MagicMock()  # stub for handlers that take supa=...
    return d


class FactRouteTests(unittest.TestCase):
    def test_what_is_ein_routes_to_agency_fact(self) -> None:
        d = _make_dispatcher()
        # EIN has no canonical column, so the answer comes from client_facts.
        d.supa.select.side_effect = [
            [{"id": "ent-1", "entity_name": "JB Noble"}],
            [{
                "id": "fact-1", "fact_label": "EIN", "fact_value": "12-3456789",
                "sensitivity": "restricted", "source": "underwriting summary",
                "confidence": "high", "source_date": "2026-05-19",
            }],
        ]
        result = d.dispatch(MagicMock(), "What is JB Noble's EIN?")
        self.assertTrue(result.ok)
        self.assertIn("12-3456789", result.message)
        # Source should cite the retrieval table, not the lookup handler.
        self.assertIn("client_facts", result.message)

    def test_phone_for_routes_to_agency_fact(self) -> None:
        d = _make_dispatcher()
        d.supa.select.return_value = [
            {"insured_name": "Joseph Washington", "phone": "+14045550142"}
        ]
        result = d.dispatch(MagicMock(), "phone for Joseph Washington")
        self.assertTrue(result.ok)
        self.assertIn("+14045550142", result.message)

    def test_renewal_date_routes_to_agency_fact(self) -> None:
        d = _make_dispatcher()
        d.supa.select.return_value = []  # nothing in the book or the facts
        result = d.dispatch(MagicMock(), "What is 3D Pumps's renewal date?")
        # Even when nothing found, the route was reached — verify it's
        # the not-found template, not lookup's response.
        self.assertIn("do not have", result.message)

    def test_unrecognized_what_does_not_hit_agency_fact(self) -> None:
        """A question without a known fact label should NOT hit agency_fact.

        Dispatcher captures handler callables at init time, so patching the
        source module doesn't intercept; instead patch fact_retriever.handle
        and assert it stays uncalled.
        """
        d = _make_dispatcher()
        with patch("hermes.commands.fact_retriever.handle") as fact_handle:
            from hermes.core.dispatcher import DispatchResult
            fact_handle.return_value = DispatchResult(True, "fact ran")
            d.dispatch(MagicMock(), "find Acme")
            fact_handle.assert_not_called()


class IntakeRouteTests(unittest.TestCase):
    def test_stage_intake_routes_to_agency_intake(self) -> None:
        d = _make_dispatcher()
        with patch("hermes.commands.agency_intake.handle") as ai_handle:
            from hermes.core.dispatcher import DispatchResult
            ai_handle.return_value = DispatchResult(
                True, "draft staged", {"draft_id": "x", "slack_blocks": [{}, {}]}
            )
            result = d.dispatch(MagicMock(), "stage intake: 3D Pumps LLC paste here")
            ai_handle.assert_called_once()
            self.assertTrue(result.ok)
            self.assertEqual(result.data["draft_id"], "x")

    def test_new_commercial_prospect_routes_to_agency_intake(self) -> None:
        d = _make_dispatcher()
        with patch("hermes.commands.agency_intake.handle") as ai_handle:
            from hermes.core.dispatcher import DispatchResult
            ai_handle.return_value = DispatchResult(True, "ok", {})
            d.dispatch(MagicMock(), "new commercial prospect: 3D Pumps")
            ai_handle.assert_called_once()

    def test_structured_hermes_block_routes_to_agency_intake(self) -> None:
        """Producer-submitted "Hermes:\n…\nMODULE:" blocks bypass the verb prefix."""
        d = _make_dispatcher()
        post = (
            "3D Pumps LLC – Full Summary | 2026-05-21 | Producer: Lamar Coates\n"
            "8 records below — Contact, Account, GL, WC, Commercial Auto, "
            "Inland Marine, CPL, Umbrella.\n\n"
            "Hermes:\n\n"
            "PRE-CHECK:\n- Searched CRM?:\n\n"
            "MODULE: contact\n"
            "ACTION: create\n"
            "RECORD NAME: Jarod Denero Mattison\n"
        )
        with patch("hermes.commands.agency_intake.handle") as ai_handle:
            from hermes.core.dispatcher import DispatchResult
            ai_handle.return_value = DispatchResult(True, "draft staged", {})
            result = d.dispatch(MagicMock(), post)
            ai_handle.assert_called_once()
            self.assertTrue(result.ok)

    def test_module_without_hermes_marker_does_not_route(self) -> None:
        """A bare `MODULE:` line without the Hermes: header should NOT match —
        avoids hijacking unrelated text that happens to contain the word."""
        d = _make_dispatcher()  # use_openai is False by default in the helper
        with patch("hermes.commands.agency_intake.handle") as ai_handle:
            d.dispatch(MagicMock(), "Some MODULE: contact discussion here")
            ai_handle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
