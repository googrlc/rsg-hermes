"""Tests for hermes.sync.nowcerts_client — NowCerts API auth and pagination."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError


class FakeResponse:
    def __init__(self, status_code: int, body=None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or str(body or "")
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._body


class NowCertsClientConstructorTests(unittest.TestCase):
    def test_raises_without_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(NowCertsClientError, "NOWCERTS_USERNAME"):
                NowCertsClient()

    def test_accepts_env_credentials(self) -> None:
        env = {
            "NOWCERTS_USERNAME": "user",
            "NOWCERTS_PASSWORD": "pass",
            "NOWCERTS_API_URL": "https://api.test.com",
        }
        with patch.dict(os.environ, env, clear=True):
            client = NowCertsClient()
            self.assertEqual(client.base_url, "https://api.test.com")


class NowCertsAuthTests(unittest.TestCase):
    @patch("hermes.sync.nowcerts_client.requests.post")
    def test_authenticate_stores_token(self, mock_post: MagicMock) -> None:
        mock_post.return_value = FakeResponse(200, {"access_token": "tok123"})
        client = NowCertsClient(
            username="user", password="pass", base_url="https://api.test.com",
        )
        token = client._authenticate()
        self.assertEqual(token, "tok123")
        self.assertEqual(client._token, "tok123")

    @patch("hermes.sync.nowcerts_client.requests.post")
    def test_authenticate_raises_on_failure(self, mock_post: MagicMock) -> None:
        mock_post.return_value = FakeResponse(401, text="bad creds")
        client = NowCertsClient(
            username="user", password="pass", base_url="https://api.test.com",
        )
        with self.assertRaisesRegex(NowCertsClientError, "auth failed"):
            client._authenticate()


class NowCertsPaginationTests(unittest.TestCase):
    @patch("hermes.sync.nowcerts_client.requests.get")
    @patch("hermes.sync.nowcerts_client.requests.post")
    def test_fetches_all_pages(self, mock_post: MagicMock, mock_get: MagicMock) -> None:
        mock_post.return_value = FakeResponse(200, {"access_token": "tok"})

        # Two pages of results, then empty
        page1 = [{"database_id": f"NC-{i}"} for i in range(50)]
        page2 = [{"database_id": f"NC-{i}"} for i in range(50, 75)]
        mock_get.side_effect = [
            FakeResponse(200, page1),
            FakeResponse(200, page2),
        ]

        client = NowCertsClient(
            username="user", password="pass", base_url="https://api.test.com",
        )
        results = client.fetch_insureds(page_size=50)
        self.assertEqual(len(results), 75)
        self.assertEqual(mock_get.call_count, 2)

    @patch("hermes.sync.nowcerts_client.requests.get")
    @patch("hermes.sync.nowcerts_client.requests.post")
    def test_retries_on_401(self, mock_post: MagicMock, mock_get: MagicMock) -> None:
        mock_post.return_value = FakeResponse(200, {"access_token": "tok"})

        # First GET returns 401, second succeeds after re-auth
        mock_get.side_effect = [
            FakeResponse(401, text="expired"),
            FakeResponse(200, [{"database_id": "NC-1"}]),
        ]

        client = NowCertsClient(
            username="user", password="pass", base_url="https://api.test.com",
        )
        results = client.fetch_insureds(page_size=50)
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
