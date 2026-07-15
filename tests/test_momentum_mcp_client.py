"""Unit tests for the Momentum (NowCerts) MCP client.

Migrated from the retired tests/test_renewal_loop.py. The client is now consumed
by the renewal executor's `note` channel (request_terms / client_follow_up).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from hermes.renewals.momentum_mcp_client import MomentumMCPClient


class FakeResponse:
    def __init__(self, status_code: int, body=None, *, text: str = "", headers=None, lines=None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or str(body or "")
        self.headers = headers or {"content-type": "application/json"}
        self._lines = lines or []
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._body

    def iter_lines(self, decode_unicode=True):
        _ = decode_unicode
        return iter(self._lines)


def test_momentum_client_posts_bearer_jsonrpc():
    session = MagicMock()
    session.post.return_value = FakeResponse(200, {"result": {"noteId": "n1"}})
    client = MomentumMCPClient(base_url="https://mcp.example.com/mcp", api_key="abc", session=session)

    result = client.manage_notes({"databaseId": "mc-1", "note": "hello"})

    assert result["noteId"] == "n1"
    kwargs = session.post.call_args.kwargs
    assert kwargs["headers"]["Authorization"].startswith("Bearer ")
    assert kwargs["headers"]["Authorization"].endswith("abc")
    assert kwargs["json"]["method"] == "tools/call"
    assert kwargs["json"]["params"]["name"] == "manage_notes"


def test_momentum_client_parses_sse():
    session = MagicMock()
    session.post.return_value = FakeResponse(
        200,
        headers={"content-type": "text/event-stream"},
        lines=['data: {"result": {"noteId": "n9"}}', "data: [DONE]"],
    )
    client = MomentumMCPClient(base_url="https://mcp.example.com/mcp", api_key="abc", session=session)

    result = client.manage_notes({"databaseId": "mc-1", "note": "hello"})

    assert result["noteId"] == "n9"


def test_momentum_client_wraps_non_dict_result():
    session = MagicMock()
    session.post.return_value = FakeResponse(200, ["ok"])
    client = MomentumMCPClient(base_url="https://mcp.example.com/mcp", api_key="abc", session=session)

    result = client.manage_notes({"databaseId": "mc-1", "note": "hello"})

    assert result == {"result": ["ok"]}
