"""Tests for the crm-fact-retriever runtime.

Covers:
  - parse_question turns NL questions into (entity, fact_label)
  - CRM canonical-field lookup wins when present
  - Falls through to client_facts when CRM field is empty
  - Restricted facts are marked accordingly
  - Multi-match returns a candidates list, not a fabricated answer
  - Not-found path returns a "would you like to capture it?" prompt
  - render() formats with source + confidence
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from hermes.commands.fact_retriever import (
    FactAnswer,
    handle,
    parse_question,
    retrieve,
)


class ParseQuestionTests(unittest.TestCase):
    def test_possessive_form(self) -> None:
        self.assertEqual(
            parse_question("What is JB Noble's EIN?"),
            ("JB Noble", "EIN"),
        )

    def test_possessive_phone(self) -> None:
        self.assertEqual(
            parse_question("What is Joseph Washington's phone number?"),
            ("Joseph Washington", "Phone"),
        )

    def test_for_form(self) -> None:
        self.assertEqual(
            parse_question("Find EIN for 3D Pumps LLC"),
            ("3D Pumps LLC", "EIN"),
        )

    def test_phone_for(self) -> None:
        self.assertEqual(
            parse_question("Phone for Jarod Mattison"),
            ("Jarod Mattison", "Phone"),
        )

    def test_renewal_date(self) -> None:
        result = parse_question("What is 3D Pumps's renewal date?")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[1], "Renewal Date")
        self.assertIn("3D Pumps", result[0])

    def test_principal_who(self) -> None:
        result = parse_question("Who is the principal for 3D Pumps LLC?")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[1], "Principal")
        self.assertEqual(result[0], "3D Pumps LLC")

    def test_unparseable(self) -> None:
        self.assertIsNone(parse_question(""))
        self.assertIsNone(parse_question("hello"))
        self.assertIsNone(parse_question("what time is it?"))


class CanonicalBookLookupTests(unittest.TestCase):
    def test_phone_resolves_via_canonical_clients(self) -> None:
        supa = MagicMock()
        supa.select.return_value = [
            {"insured_name": "Joseph Washington", "phone": "+14045550142"}
        ]
        result = retrieve(
            supa,
            entity_name="Joseph Washington",
            fact_label="Phone",
        )
        self.assertTrue(result.found)
        self.assertEqual(result.fact_value, "+14045550142")
        self.assertEqual(result.source, "canonical book canonical_clients.phone")
        self.assertEqual(result.sensitivity, "standard")
        self.assertEqual(result.confidence, "high")

    def test_renewal_date_resolves_via_canonical_policies(self) -> None:
        supa = MagicMock()
        supa.select.return_value = [
            {"client_name": "3D Pumps LLC", "expiration_date": "2026-09-01"}
        ]
        result = retrieve(
            supa,
            entity_name="3D Pumps",
            fact_label="Renewal Date",
        )
        self.assertTrue(result.found)
        self.assertEqual(result.fact_value, "2026-09-01")
        self.assertIn("canonical_policies.expiration_date", result.source)

    def test_label_with_no_canonical_column_falls_through_to_client_facts(self) -> None:
        """EIN has no canonical column — intake records it, so tier 2 answers."""
        supa = MagicMock()
        supa.select.side_effect = [
            [{"id": "ent-1", "entity_name": "3D Pumps LLC"}],   # client_entities
            [{                                                  # client_facts
                "id": "fact-1",
                "fact_label": "EIN",
                "fact_value": "33-3725730",
                "sensitivity": "restricted",
                "source": "underwriting summary",
                "confidence": "high",
                "source_date": "2026-05-19",
            }],
        ]
        result = retrieve(
            supa,
            entity_name="3D Pumps",
            fact_label="EIN",
        )
        self.assertTrue(result.found)
        self.assertEqual(result.fact_value, "33-3725730")
        self.assertIn("client_facts", result.source)
        self.assertIn("underwriting summary", result.source)
        self.assertEqual(result.sensitivity, "restricted")

    def test_empty_canonical_row_falls_through(self) -> None:
        supa = MagicMock()
        supa.select.side_effect = [
            [{"insured_name": "3D Pumps LLC", "phone": None}],  # canonical_clients
            [{"insured_name": "3D Pumps LLC", "cell_phone": None}],
            [{"id": "ent-1", "entity_name": "3D Pumps LLC"}],   # client_entities
            [{
                "id": "fact-1",
                "fact_label": "Phone",
                "fact_value": "+14045550199",
                "sensitivity": "standard",
                "source": "intake call",
                "confidence": "high",
                "source_date": "2026-05-19",
            }],
        ]
        result = retrieve(
            supa,
            entity_name="3D Pumps",
            fact_label="Phone",
        )
        self.assertTrue(result.found)
        self.assertEqual(result.fact_value, "+14045550199")
        self.assertIn("client_facts", result.source)


class NotFoundPathTests(unittest.TestCase):
    def test_returns_capture_prompt(self) -> None:
        supa = MagicMock()
        supa.select.return_value = []     # nothing anywhere
        result = retrieve(
            supa,
            entity_name="Mystery Co",
            fact_label="EIN",
        )
        self.assertFalse(result.found)
        rendered = result.render()
        self.assertIn("do not have", rendered)
        self.assertIn("intake", rendered)


class CandidatesTests(unittest.TestCase):
    def test_multi_match_returns_candidates(self) -> None:
        supa = MagicMock()
        # search_entities returns 2 candidates; both have no fact rows
        supa.select.side_effect = [
            [
                {"id": "e1", "entity_name": "JB Noble Construction LLC"},
                {"id": "e2", "entity_name": "JB Noble Benefits"},
            ],
            [],  # facts for e1
            [],  # facts for e2
        ]
        result = retrieve(
            supa,
            entity_name="JB Noble",
            fact_label="EIN",
        )
        self.assertFalse(result.found)
        self.assertIn("do not have", result.render())


class HandleEntrypointTests(unittest.TestCase):
    def test_handle_returns_dispatchresult_with_source(self) -> None:
        supa = MagicMock()
        supa.select.return_value = [
            {"client_name": "3D Pumps LLC", "expiration_date": "2026-09-01"}
        ]
        result = handle("What is 3D Pumps's renewal date?", supa=supa)
        self.assertTrue(result.ok)
        self.assertIn("2026-09-01", result.message)
        self.assertIn("canonical_policies.expiration_date", result.message)
        self.assertIn("Confidence: high", result.message)
        self.assertEqual(result.data["fact_label"], "Renewal Date")

    def test_handle_unparseable_question(self) -> None:
        result = handle("I don't know what to ask")
        self.assertFalse(result.ok)
        self.assertIn("couldn't tell", result.message.lower())


class FactAnswerRenderTests(unittest.TestCase):
    def test_render_includes_restricted_marker(self) -> None:
        answer = FactAnswer(
            found=True,
            entity="3D Pumps LLC",
            fact_label="EIN",
            fact_value="33-3725730",
            source="client_facts (underwriting summary, 2026-05-19)",
            confidence="high",
            sensitivity="restricted",
        )
        rendered = answer.render()
        self.assertIn("33-3725730", rendered)
        self.assertIn("RESTRICTED", rendered)
        self.assertIn("client_facts", rendered)

    def test_render_not_found(self) -> None:
        answer = FactAnswer(
            found=False,
            entity="Mystery Co",
            fact_label="EIN",
            fact_value=None,
            source="not_found",
        )
        rendered = answer.render()
        self.assertIn("do not have", rendered)
        self.assertIn("intake", rendered)


if __name__ == "__main__":
    unittest.main()
