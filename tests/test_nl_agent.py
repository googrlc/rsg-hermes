"""Tests for the NL agent module."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from hermes.core.nl_agent import (
    _exec_search,
    _exec_get_field,
    _exec_report,
    _exec_total_premium,
    _exec_create,
    _exec_update,
    _exec_merge,
    ask,
)
from hermes.core.dispatcher import DispatchResult


class MockClient:
    """Minimal mock EspoClient for NL agent tests."""

    def __init__(self) -> None:
        self.search_results: list[dict] = []
        self.create_result: dict = {"id": "new123", "name": "Test"}
        self.update_result: dict = {"id": "upd123", "name": "Updated"}

    def search(self, entity: str, query: str, **kwargs) -> list[dict]:
        return self.search_results

    def create(self, entity: str, payload: dict) -> dict:
        return self.create_result

    def update(self, entity: str, record_id: str, payload: dict) -> dict:
        return self.update_result

    def get(self, path: str, **kwargs) -> dict:
        return {"list": self.search_results}

    def ping(self) -> dict:
        return {"user": {"id": "user1"}}


class SearchTests(unittest.TestCase):
    def test_search_empty_results(self) -> None:
        client = MockClient()
        result = _exec_search(client, {"entity": "Account", "query": "Nonexistent"})
        self.assertTrue(result.ok)
        self.assertIn("No Account records", result.message)

    def test_search_with_results(self) -> None:
        client = MockClient()
        client.search_results = [
            {"id": "abc", "name": "Acme Corp", "fein": "12-3456789"},
            {"id": "def", "name": "Acme LLC"},
        ]
        result = _exec_search(client, {"entity": "Account", "query": "Acme"})
        self.assertTrue(result.ok)
        self.assertIn("Acme Corp", result.message)
        self.assertIn("2 results", result.message)
        self.assertEqual(len(result.data["results"]), 2)


class CreateTests(unittest.TestCase):
    def test_create_requires_confirmation(self) -> None:
        client = MockClient()
        result = _exec_create(
            client,
            {"entity": "Contact", "fields": {"firstName": "John", "lastName": "Smith"}},
            confirmed=False,
        )
        self.assertFalse(result.ok)
        self.assertIn("Confirm to proceed", result.message)
        self.assertTrue(result.data["requires_confirmation"])

    def test_create_confirmed(self) -> None:
        client = MockClient()
        result = _exec_create(
            client,
            {"entity": "Contact", "fields": {"firstName": "John", "lastName": "Smith"}},
            confirmed=True,
        )
        self.assertTrue(result.ok)
        self.assertIn("Created Contact", result.message)


class UpdateTests(unittest.TestCase):
    def test_update_requires_confirmation(self) -> None:
        client = MockClient()
        result = _exec_update(
            client,
            {"entity": "Account", "record_id": "abc123", "fields": {"fein": "99-1234567"}},
            confirmed=False,
        )
        self.assertFalse(result.ok)
        self.assertIn("Confirm to proceed", result.message)

    def test_update_confirmed(self) -> None:
        client = MockClient()
        result = _exec_update(
            client,
            {"entity": "Account", "record_id": "abc123", "fields": {"fein": "99-1234567"}},
            confirmed=True,
        )
        self.assertTrue(result.ok)
        self.assertIn("Updated Account", result.message)


class MergeTests(unittest.TestCase):
    def test_merge_requires_confirmation(self) -> None:
        client = MockClient()
        result = _exec_merge(
            client,
            {"entity": "Contact", "source_id": "src1", "target_id": "tgt1"},
            confirmed=False,
        )
        self.assertFalse(result.ok)
        self.assertIn("Confirm to proceed", result.message)


class AskTests(unittest.TestCase):
    @patch.dict("os.environ", {"OPENAI_API_KEY": ""})
    def test_ask_no_api_key(self) -> None:
        client = MockClient()
        result = ask(client, "find Acme")
        self.assertFalse(result.ok)
        self.assertIn("API key not configured", result.message)


if __name__ == "__main__":
    unittest.main()
