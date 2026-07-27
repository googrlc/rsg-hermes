"""Tests for hermes.sync.nowcerts_client — NowCerts API auth and pagination."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError


class FakeResponse:
    def __init__(self, status_code: int, body=None, text: str = "", content=None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or str(body or "")
        self.ok = 200 <= status_code < 300
        # _post/_patch return json only when the response has a body
        self.content = content if content is not None else (b"{}" if body else b"")

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


class NowCertsWriteMethodTests(unittest.TestCase):
    """The CRM->NowCerts write-back endpoints (task ledger + no-override insured)."""

    def _client(self) -> NowCertsClient:
        return NowCertsClient(
            username="user", password="pass", base_url="https://api.test.com",
        )

    @patch("hermes.sync.nowcerts_client.requests.post")
    def test_insert_task_posts_to_zapier_endpoint(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = [
            FakeResponse(200, {"access_token": "tok"}),   # auth
            FakeResponse(200, {"database_id": "TASK-1"}),  # write
        ]
        payload = {
            "title": "COI request",
            "status": "Open",
            "priority": "Medium",
            "due_date": "2026-07-11",
            "category_name": "Certificate of Insurance (COI)",
            "insured_database_id": "INS-GUID",
        }
        result = self._client().insert_task(payload)
        self.assertEqual(result, {"database_id": "TASK-1"})
        write_call = mock_post.call_args_list[-1]
        self.assertEqual(write_call.args[0], "https://api.test.com/api/Zapier/InsertTask")
        self.assertEqual(write_call.kwargs["json"], payload)

    @patch("hermes.sync.nowcerts_client.requests.post")
    def test_update_task_posts_to_update_endpoint(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = [
            FakeResponse(200, {"access_token": "tok"}),
            FakeResponse(200, {}),
        ]
        self._client().update_task({"database_id": "TASK-1", "status": "Closed"})
        self.assertEqual(
            mock_post.call_args_list[-1].args[0],
            "https://api.test.com/api/Zapier/UpdateTask",
        )

    @patch("hermes.sync.nowcerts_client.requests.post")
    def test_insert_insured_no_override_endpoint(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = [
            FakeResponse(200, {"access_token": "tok"}),
            FakeResponse(200, {"DatabaseId": "INS-9"}),
        ]
        out = self._client().insert_insured_no_override({"CommercialName": "Acme LLC"})
        self.assertEqual(out, {"DatabaseId": "INS-9"})
        self.assertEqual(
            mock_post.call_args_list[-1].args[0],
            "https://api.test.com/api/Insured/InsertNoOverride",
        )

    @patch("hermes.sync.nowcerts_client.requests.post")
    def test_insert_task_retries_on_401(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = [
            FakeResponse(200, {"access_token": "tok"}),    # initial auth
            FakeResponse(401, text="expired"),             # write -> 401
            FakeResponse(200, {"access_token": "tok2"}),   # re-auth
            FakeResponse(200, {"database_id": "TASK-2"}),  # write retry ok
        ]
        result = self._client().insert_task(
            {"title": "x", "status": "Open", "priority": "Low", "due_date": "2026-07-11"}
        )
        self.assertEqual(result, {"database_id": "TASK-2"})
        self.assertEqual(mock_post.call_count, 4)


if __name__ == "__main__":
    unittest.main()


class SharedClientAndTokenReuseTests(unittest.TestCase):
    """Regression cover for the stall diagnosed 2026-07-27.

    NowCerts' password grant measures ~26s against the live API. That is only
    survivable if the token is fetched once and shared; every call site building
    its own client re-paid it, which froze the API and timed out the MCP tools.
    """

    ENV = {"NOWCERTS_USERNAME": "u@risksolutionsgroup.net", "NOWCERTS_PASSWORD": "p"}

    def setUp(self) -> None:
        import hermes.sync.nowcerts_client as mod
        self.mod = mod
        mod._shared = None            # a shared singleton must not leak between tests
        self.addCleanup(setattr, mod, "_shared", None)

    def test_get_client_returns_one_shared_instance(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=True):
            self.assertIs(self.mod.get_client(), self.mod.get_client())

    def test_token_is_fetched_once_and_reused(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=True):
            c = NowCertsClient()
        with patch("requests.post", return_value=FakeResponse(200, {"access_token": "t"})) as post:
            for _ in range(5):
                headers = c._headers()
        post.assert_called_once()
        self.assertEqual(headers["Authorization"], "Bearer t")

    def test_auth_gets_a_longer_timeout_than_a_data_read(self) -> None:
        """A 30s ceiling sat on top of a ~26s grant and made auth a coin flip."""
        with patch.dict(os.environ, self.ENV, clear=True):
            c = NowCertsClient()
        self.assertGreater(c.auth_timeout, c.timeout)
        with patch("requests.post", return_value=FakeResponse(200, {"access_token": "t"})) as post:
            c._authenticate()
        self.assertEqual(post.call_args.kwargs["timeout"], c.auth_timeout)

    def test_auth_timeout_never_drops_below_an_explicit_timeout(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=True):
            c = NowCertsClient(timeout=120.0)
        self.assertGreaterEqual(c.auth_timeout, 120.0)


class ReadTimeoutRetryTests(unittest.TestCase):
    """#264 set `self.retries` and never used it — the commit claimed a retry the
    code did not have. Measured live: the same request 10x succeeded 4/10, so a
    5-page book pull landed ~1% of the time. These pin the retry."""

    ENV = {"NOWCERTS_USERNAME": "u@risksolutionsgroup.net", "NOWCERTS_PASSWORD": "p"}

    def _client(self, **kw):
        with patch.dict(os.environ, self.ENV, clear=True):
            c = NowCertsClient(**kw)
        c._token = "t"          # skip auth
        return c

    def test_a_timeout_is_retried_and_can_succeed(self) -> None:
        import requests as rq
        c = self._client(retries=3)
        seq = [rq.Timeout("stalled"), rq.Timeout("stalled"), FakeResponse(200, {"value": [1, 2]})]
        with patch("requests.get", side_effect=seq) as g, patch("time.sleep"):
            self.assertEqual(c._get("/api/PolicyDetailList"), {"value": [1, 2]})
        self.assertEqual(g.call_count, 3)

    def test_exhausting_the_retries_raises(self) -> None:
        import requests as rq
        c = self._client(retries=2)
        with patch("requests.get", side_effect=rq.Timeout("stalled")) as g, patch("time.sleep"):
            with self.assertRaisesRegex(NowCertsClientError, "3 attempts all timed out"):
                c._get("/api/PolicyDetailList")
        self.assertEqual(g.call_count, 3)

    def test_backoff_grows_between_attempts(self) -> None:
        import requests as rq
        c = self._client(retries=3)
        with patch("requests.get", side_effect=rq.Timeout("x")), patch("time.sleep") as sl:
            with self.assertRaises(NowCertsClientError):
                c._get("/api/PolicyDetailList")
        waits = [a.args[0] for a in sl.call_args_list]
        self.assertEqual(waits, sorted(waits))
        self.assertGreater(waits[-1], waits[0])

    def test_a_401_costs_a_reauth_not_the_retry_budget(self) -> None:
        c = self._client(retries=1)
        with patch("requests.get", side_effect=[FakeResponse(401), FakeResponse(200, {"ok": 1})]), \
             patch.object(NowCertsClient, "_authenticate", return_value="t2") as auth:
            self.assertEqual(c._get("/api/x"), {"ok": 1})
        auth.assert_called_once()

    def test_an_http_error_is_not_retried(self) -> None:
        """A 500 is an answer, not a stall — retrying it just multiplies load."""
        c = self._client(retries=3)
        with patch("requests.get", return_value=FakeResponse(500, text="boom")) as g:
            with self.assertRaises(NowCertsClientError):
                c._get("/api/x")
        self.assertEqual(g.call_count, 1)
