"""Tests for hermes.sync.metadata — payload conforming + name normalization."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from hermes.core.client import EspoClientError
from hermes.sync.metadata import (
    camel_to_snake,
    conform_payload_to_metadata,
    normalize_name,
    resolve_field_name,
    snake_to_camel,
)


def _espo_with_fields(
    *field_names: str, links: dict | None = None, entity: str = "Account",
) -> MagicMock:
    espo = MagicMock()
    espo.get_metadata.return_value = {
        entity: {
            "fields": {name: {"type": "varchar"} for name in field_names},
            "links": links or {},
        }
    }
    return espo


class CaseHelperTests(unittest.TestCase):
    def test_snake_to_camel(self) -> None:
        self.assertEqual(snake_to_camel("momentum_client_id"), "momentumClientId")
        self.assertEqual(snake_to_camel("name"), "name")

    def test_camel_to_snake(self) -> None:
        self.assertEqual(camel_to_snake("momentumClientId"), "momentum_client_id")
        self.assertEqual(camel_to_snake("name"), "name")


class NormalizeNameTests(unittest.TestCase):
    def test_strips_suffix_and_punctuation(self) -> None:
        self.assertEqual(normalize_name("Shamira Douglas, LLC"), "shamira douglas")
        self.assertEqual(normalize_name("shamira douglas llc"), "shamira douglas")

    def test_equal_across_variants(self) -> None:
        self.assertEqual(
            normalize_name("Acme Corp., Inc."),
            normalize_name("ACME  corporation"),
        )

    def test_empty(self) -> None:
        self.assertEqual(normalize_name(None), "")
        self.assertEqual(normalize_name(""), "")


class ConformPayloadTests(unittest.TestCase):
    def test_drops_unknown_field(self) -> None:
        espo = _espo_with_fields("name", "fein")
        out = conform_payload_to_metadata(
            espo, "Account", {"name": "Acme", "years_in_business": 12},
        )
        self.assertEqual(out, {"name": "Acme"})

    def test_remaps_snake_to_existing_camel(self) -> None:
        espo = _espo_with_fields("name", "momentumClientId")
        out = conform_payload_to_metadata(
            espo, "Account", {"name": "Acme", "momentum_client_id": "NC-1"},
        )
        self.assertEqual(out, {"name": "Acme", "momentumClientId": "NC-1"})

    def test_keeps_existing_snake_field(self) -> None:
        # Some live Espo fields ARE snake_case; do not force-camel them.
        espo = _espo_with_fields("name", "account_type")
        out = conform_payload_to_metadata(
            espo, "Account", {"name": "Acme", "account_type": "Commercial Lines"},
        )
        self.assertEqual(out, {"name": "Acme", "account_type": "Commercial Lines"})

    def test_allows_link_id_fields(self) -> None:
        espo = _espo_with_fields(
            "name", links={"account": {}}, entity="Contact",
        )
        out = conform_payload_to_metadata(
            espo, "Contact", {"name": "Jane", "accountId": "a-1"},
        )
        self.assertEqual(out, {"name": "Jane", "accountId": "a-1"})

    def test_fails_open_when_metadata_unavailable(self) -> None:
        espo = MagicMock()
        espo.get_metadata.side_effect = EspoClientError("boom")
        payload = {"name": "Acme", "whatever": 1}
        self.assertEqual(conform_payload_to_metadata(espo, "Account", payload), payload)


class ResolveFieldNameTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        espo = _espo_with_fields("momentum_client_id")
        self.assertEqual(
            resolve_field_name(espo, "Account", "momentum_client_id"),
            "momentum_client_id",
        )

    def test_camel_variant(self) -> None:
        espo = _espo_with_fields("momentumClientId")
        self.assertEqual(
            resolve_field_name(espo, "Account", "momentum_client_id"),
            "momentumClientId",
        )

    def test_absent_returns_none(self) -> None:
        espo = _espo_with_fields("name")
        self.assertIsNone(resolve_field_name(espo, "Account", "momentum_client_id"))

    def test_fails_open_returns_candidate(self) -> None:
        espo = MagicMock()
        espo.get_metadata.side_effect = EspoClientError("boom")
        self.assertEqual(resolve_field_name(espo, "Account", "fein"), "fein")


if __name__ == "__main__":
    unittest.main()
