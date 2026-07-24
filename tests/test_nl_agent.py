"""Tests for the NL agent module."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from hermes.core.nl_agent import (
    _exec_report,
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


class AskTests(unittest.TestCase):
    @patch.dict("os.environ", {"OPENAI_API_KEY": ""})
    def test_ask_no_api_key(self) -> None:
        client = MockClient()
        result = ask(client, "find Acme")
        self.assertFalse(result.ok)
        self.assertIn("API key not configured", result.message)


if __name__ == "__main__":
    unittest.main()
