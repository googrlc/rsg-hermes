"""An MCP refusal must never look like an empty result.

MCP-over-HTTP always answers HTTP 200. Auth failures, bad tool names and server
errors all come back inside the JSON-RPC body as an `error` object. Code that
checks the status code sees success and carries on with nothing.

That is the failure mode worth engineering against here, because for this
estate an empty result is a *plausible answer*. "No carriers write this risk"
reads as a declination. "No tasks" reads as nothing to do. A caller cannot tell
a refusal from a real empty list unless the client refuses to let it.

The live door was verified returning exactly this: HTTP 200 with
`{"error": {"code": -32001, "message": "Unauthorized"}}`.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from hermes_app.ams import AmsClient, AmsError


class _Resp:
    """Minimal stand-in for urlopen's context manager."""

    def __init__(self, payload):
        self._raw = payload if isinstance(payload, str) else json.dumps(payload)

    def read(self):
        return self._raw.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _client():
    return AmsClient(base_url="http://ams.test/mcp", token="t", timeout=1)


def test_an_unauthorized_body_raises_instead_of_returning_nothing() -> None:
    """The exact shape the live door returns: HTTP 200, error in the body."""
    body = {"jsonrpc": "2.0", "id": None,
            "error": {"code": -32001, "message": "Unauthorized"}}
    with patch("urllib.request.urlopen", return_value=_Resp(body)):
        with pytest.raises(AmsError) as exc:
            _client().call("insert_task_tool", {"subject": "x"})
    msg = str(exc.value)
    assert "unauthorized" in msg.lower()
    assert "NOT an empty result" in msg, (
        "the message must say this is a refusal — that is the whole point"
    )


def test_any_other_error_in_the_body_also_raises() -> None:
    body = {"jsonrpc": "2.0", "id": 1,
            "error": {"code": -32602, "message": "unknown tool: insert_taks_tool"}}
    with patch("urllib.request.urlopen", return_value=_Resp(body)):
        with pytest.raises(AmsError) as exc:
            _client().call("insert_taks_tool")
    # A typo'd tool name must surface as a failure, not as "nothing found".
    assert "-32602" in str(exc.value) or "unknown tool" in str(exc.value)


def test_a_successful_call_returns_the_result() -> None:
    body = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"text": "ok"}]}}
    with patch("urllib.request.urlopen", return_value=_Resp(body)):
        assert _client().call("get_task_list_tool") == {"content": [{"text": "ok"}]}


def test_a_genuinely_empty_result_is_returned_not_raised() -> None:
    """The other half: a real empty list must pass through untouched, or the
    client would turn 'no matches' into an error."""
    body = {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}
    with patch("urllib.request.urlopen", return_value=_Resp(body)):
        assert _client().call("get_policy_list_tool") == {"content": []}


def test_non_json_from_the_door_raises_with_a_readable_excerpt() -> None:
    with patch("urllib.request.urlopen", return_value=_Resp("<html>502 gateway</html>")):
        with pytest.raises(AmsError) as exc:
            _client().call("get_task_list_tool")
    assert "non-JSON" in str(exc.value)


def test_the_bearer_is_sent() -> None:
    seen = {}

    def _capture(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        seen["url"] = req.full_url
        return _Resp({"jsonrpc": "2.0", "id": 1, "result": {}})

    with patch("urllib.request.urlopen", side_effect=_capture):
        _client().call("get_task_list_tool")
    assert seen["auth"] == "Bearer t"
    assert seen["url"] == "http://ams.test/mcp"


def test_the_call_is_a_jsonrpc_tools_call_naming_the_tool() -> None:
    seen = {}

    def _capture(req, timeout=None):
        seen.update(json.loads(req.data.decode("utf-8")))
        return _Resp({"jsonrpc": "2.0", "id": 1, "result": {}})

    with patch("urllib.request.urlopen", side_effect=_capture):
        _client().call("insert_policy_tool", {"policyNumber": "P-1"})
    assert seen["method"] == "tools/call"
    assert seen["params"]["name"] == "insert_policy_tool"
    assert seen["params"]["arguments"] == {"policyNumber": "P-1"}


def test_an_empty_tool_name_is_refused_before_any_call() -> None:
    with patch("urllib.request.urlopen", side_effect=AssertionError("should not call")):
        with pytest.raises(ValueError):
            _client().call("")
