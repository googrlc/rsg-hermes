from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import requests

from hermes.core.client import EspoClient, EspoClientError


class FakeResponse:
    def __init__(self, status_code: int, body: dict | list | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or str(body or "")
        self.content = b"1" if body is not None else b""
        self.ok = 200 <= status_code < 300

    def json(self) -> dict | list:
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, outcomes: list[FakeResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class EspoClientReliabilityTests(unittest.TestCase):
    def test_constructor_rejects_missing_base_url_even_when_api_key_exists(self) -> None:
        with patch.dict(os.environ, {"ESPO_API_KEY": "key"}, clear=True):
            with self.assertRaisesRegex(EspoClientError, "ESPO_URL"):
                EspoClient()

    def test_get_retries_transient_status_before_succeeding(self) -> None:
        session = FakeSession(
            [
                FakeResponse(503, {"message": "temporarily unavailable"}),
                FakeResponse(200, {"list": [], "total": 0}),
            ]
        )
        client = EspoClient(
            base_url="https://crm.example",
            api_key="key",
            timeout=1,
            session=session,
            retry_sleep=0,
        )

        body = client.get("Account", params={"maxSize": 1})

        self.assertEqual(body, {"list": [], "total": 0})
        self.assertEqual(len(session.calls), 2)

    def test_get_caps_oversized_list_requests_to_espo_limit(self) -> None:
        session = FakeSession([FakeResponse(200, {"list": [], "total": 0})])
        client = EspoClient(
            base_url="https://crm.example",
            api_key="key",
            timeout=1,
            session=session,
            retry_sleep=0,
            max_list_size=200,
        )

        client.get("Account", params={"maxSize": 500})

        self.assertEqual(session.calls[0]["params"]["maxSize"], "200")

    def test_post_does_not_retry_to_avoid_duplicate_writes(self) -> None:
        session = FakeSession(
            [
                requests.ConnectionError("connection dropped after send"),
                FakeResponse(200, {"id": "should-not-be-used"}),
            ]
        )
        client = EspoClient(
            base_url="https://crm.example",
            api_key="key",
            timeout=1,
            session=session,
            retry_sleep=0,
        )

        with self.assertRaisesRegex(EspoClientError, "Network error"):
            client.post("Contact", json={"name": "Jane Doe"})

        self.assertEqual(len(session.calls), 1)

    def test_forbidden_error_names_permission_root_cause(self) -> None:
        session = FakeSession([FakeResponse(403, {"message": "Forbidden"})])
        client = EspoClient(
            base_url="https://crm.example",
            api_key="key",
            timeout=1,
            session=session,
            retry_sleep=0,
        )

        with self.assertRaises(EspoClientError) as ctx:
            client.get("Account", params={"maxSize": 1})

        self.assertIn("403 GET Account", str(ctx.exception))
        self.assertIn("EspoCRM API user lacks permission", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
