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
        client = MagicMock()
        client.search.return_value = [
            {"id": "acc-1", "name": "JB Noble", "fein": "12-3456789"}
        ]
        result = d.dispatch(client, "What is JB Noble's EIN?")
        self.assertTrue(result.ok)
        self.assertIn("12-3456789", result.message)
        # Source should cite the CRM canonical field, not the lookup handler.
        self.assertIn("EspoCRM Account.fein", result.message)

    def test_phone_for_routes_to_agency_fact(self) -> None:
        d = _make_dispatcher()
        client = MagicMock()
        client.search.return_value = [
            {"id": "c-1", "name": "Joseph Washington", "phoneNumber": "+14045550142"}
        ]
        result = d.dispatch(client, "phone for Joseph Washington")
        self.assertTrue(result.ok)
        self.assertIn("+14045550142", result.message)

    def test_renewal_date_routes_to_agency_fact(self) -> None:
        d = _make_dispatcher()
        client = MagicMock()
        client.search.return_value = []
        d.supa.select.return_value = []  # no entity match either
        result = d.dispatch(client, "What is 3D Pumps's renewal date?")
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


if __name__ == "__main__":
    unittest.main()
