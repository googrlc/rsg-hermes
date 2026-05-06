"""Tests for merge command and ping health check."""

from __future__ import annotations

import unittest

from hermes.commands.merge import handle
from hermes.core.dispatcher import Dispatcher


class MergeClient:
    """Minimal mock EspoClient for merge tests."""

    def __init__(self) -> None:
        self.post_calls: list[tuple[str, dict]] = []
        self.get_results: dict[str, dict] = {}

    def post(self, path: str, json: dict | None = None) -> dict:
        self.post_calls.append((path, json or {}))
        return {"ok": True}

    def get(self, path: str, params: dict | None = None) -> dict:
        if path in self.get_results:
            return self.get_results[path]
        if "/" in path and not path.startswith("Contact"):
            raise Exception("Not found")
        return {"id": path.split("/")[-1], "name": "Test Record"}


class MergeTests(unittest.TestCase):
    def test_merge_contact_explicit(self) -> None:
        client = MergeClient()
        client.get_results["Contact/abc123"] = {"id": "abc123", "name": "Alice Smith"}
        client.get_results["Contact/def456"] = {"id": "def456", "name": "Alicia Smith"}
        result = handle(client, "merge contact abc123 into def456")
        self.assertTrue(result.ok)
        self.assertIn("Merged", result.message)
        self.assertIn("def456", result.message)
        self.assertEqual(len(client.post_calls), 1)
        path, payload = client.post_calls[0]
        self.assertEqual(path, "Action")
        self.assertEqual(payload["action"], "merge")
        self.assertEqual(payload["id"], "def456")
        self.assertEqual(payload["data"]["sourceIdList"], ["abc123"])

    def test_merge_unknown_entity(self) -> None:
        client = MergeClient()
        result = handle(client, "merge widget abc123 into def456")
        self.assertFalse(result.ok)
        self.assertIn("Unknown entity", result.message)

    def test_merge_natural_language(self) -> None:
        client = MergeClient()
        client.get_results["Contact/aaa111bbb222"] = {"id": "aaa111bbb222", "name": "Name A"}
        client.get_results["Contact/ccc333ddd444"] = {"id": "ccc333ddd444", "name": "Name B"}
        text = "ANISSA TAWIAH (id: aaa111bbb222) -> Anissa Tawiah (id: ccc333ddd444) can be merged"
        result = handle(client, text)
        self.assertTrue(result.ok)
        self.assertIn("Merged", result.message)

    def test_merge_bad_format(self) -> None:
        client = MergeClient()
        result = handle(client, "merge everything together")
        self.assertFalse(result.ok)
        self.assertIn("Could not parse", result.message)

    def test_ping_via_dispatcher(self) -> None:
        dispatcher = Dispatcher(use_openai=False)
        client = MergeClient()
        result = dispatcher.dispatch(client, "ping")
        self.assertTrue(result.ok)
        self.assertIn("online", result.message)

    def test_ping_variants(self) -> None:
        dispatcher = Dispatcher(use_openai=False)
        client = MergeClient()
        for cmd in ("ping", "health", "status", "  PING  "):
            result = dispatcher.dispatch(client, cmd)
            self.assertTrue(result.ok, f"Failed for: {cmd!r}")

    def test_merge_routed_by_dispatcher(self) -> None:
        dispatcher = Dispatcher(use_openai=False)
        client = MergeClient()
        client.get_results["Contact/abc123"] = {"id": "abc123", "name": "Alice"}
        client.get_results["Contact/def456"] = {"id": "def456", "name": "Alicia"}
        result = dispatcher.dispatch(client, "merge contact abc123 into def456")
        self.assertTrue(result.ok)
        self.assertIn("Merged", result.message)


if __name__ == "__main__":
    unittest.main()
