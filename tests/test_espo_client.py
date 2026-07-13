from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import requests

from hermes.core.client import EspoClient, EspoClientError


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        body: dict | list | None = None,
        text: str = "",
        headers: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or str(body or "")
        self.content = b"1" if body is not None else b""
        self.ok = 200 <= status_code < 300
        self.headers = headers or {}

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

        self.assertIn(("maxSize", "200"), session.calls[0]["params"])

    def test_get_where_clause_is_bracket_encoded_not_json(self) -> None:
        # EspoCRM ignores JSON-encoded `where` and silently returns the unfiltered
        # first record — leaf values must be sent as where[0][type]=... pairs.
        session = FakeSession([FakeResponse(200, {"list": [], "total": 0})])
        client = EspoClient(
            base_url="https://crm.example",
            api_key="key",
            timeout=1,
            session=session,
            retry_sleep=0,
        )

        client.get(
            "Account",
            params={
                "maxSize": 1,
                "select": "id,name",
                "where": [
                    {"type": "equals", "attribute": "name", "value": "Acme"},
                ],
            },
        )

        sent = session.calls[0]["params"]
        self.assertIn(("where[0][type]", "equals"), sent)
        self.assertIn(("where[0][attribute]", "name"), sent)
        self.assertIn(("where[0][value]", "Acme"), sent)
        # Guard against regression to JSON encoding.
        self.assertFalse(any(k == "where" for k, _ in sent))

    def test_get_nested_or_where_recurses_into_value_list(self) -> None:
        session = FakeSession([FakeResponse(200, {"list": [], "total": 0})])
        client = EspoClient(
            base_url="https://crm.example",
            api_key="key",
            timeout=1,
            session=session,
            retry_sleep=0,
        )

        client.get(
            "Contact",
            params={
                "where": [
                    {
                        "type": "or",
                        "value": [
                            {"type": "contains", "attribute": "name", "value": "ada"},
                            {"type": "equals", "attribute": "emailAddress", "value": "ada@x"},
                        ],
                    }
                ],
            },
        )

        sent = session.calls[0]["params"]
        self.assertIn(("where[0][type]", "or"), sent)
        self.assertIn(("where[0][value][0][type]", "contains"), sent)
        self.assertIn(("where[0][value][0][attribute]", "name"), sent)
        self.assertIn(("where[0][value][1][attribute]", "emailAddress"), sent)

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


class TypedErrorCaptureTests(unittest.TestCase):
    def _client(self, response: FakeResponse) -> EspoClient:
        return EspoClient(
            base_url="https://crm.example",
            api_key="key",
            timeout=1,
            session=FakeSession([response]),
            retry_sleep=0,
        )

    def test_x_status_reason_surfaced_when_body_empty(self) -> None:
        # The classic "Body: {}" case — real cause is only in the header.
        client = self._client(
            FakeResponse(
                400, {},
                headers={"X-Status-Reason": "field 'phoneNumber' is not valid"},
            )
        )
        with self.assertRaises(EspoClientError) as ctx:
            client.put("Account/abc", json={"phoneNumber": "bad"})
        exc = ctx.exception
        self.assertEqual(exc.status_code, 400)
        self.assertEqual(exc.reason, "field 'phoneNumber' is not valid")
        self.assertEqual(exc.category, "validation_400")
        self.assertIn("Reason: field 'phoneNumber' is not valid", str(exc))

    def test_409_categorized_as_conflict(self) -> None:
        client = self._client(FakeResponse(409, [{"id": "dup"}]))
        with self.assertRaises(EspoClientError) as ctx:
            client.post("Account", json={"name": "Dup"})
        self.assertEqual(ctx.exception.category, "conflict_409")

    def test_404_categorized_as_missing(self) -> None:
        client = self._client(FakeResponse(404, {}))
        with self.assertRaises(EspoClientError) as ctx:
            client.put("Account/gone", json={"name": "X"})
        self.assertEqual(ctx.exception.category, "missing_404")

    def test_message_keeps_status_prefix_for_matchers(self) -> None:
        client = self._client(FakeResponse(400, {}))
        with self.assertRaises(EspoClientError) as ctx:
            client.put("Account/abc", json={})
        # Downstream non-retryable matcher keys on the "<status> <METHOD>" prefix.
        self.assertTrue(str(ctx.exception).startswith("400 PUT Account/abc"))


if __name__ == "__main__":
    unittest.main()
