"""Reach a NowCerts module through Hermes.

Each app works a different NowCerts module — cases works tasks, renewals works
policies, intake works insureds and opportunities — and each should be able to
change how it does that without a release that also ships to the others.

The way to get that is NOT to give every app a copy of a hand-rolled client.
NowCerts already has an MCP with 95 tools (`insert_task_tool`,
`insert_policy_tool`, `insert_opportunity_tool`, `get_insured_details_tool`, and
so on) — far more of the AMS than the ~20 methods
`hermes_integrations.nowcerts_client` covers. An app names the tool for its
module and calls it.

It goes through Hermes rather than at the AMS directly, because Hermes is the
runner and the one door: it holds the credential, and the NowCerts password
grant costs ~26 seconds, so one process should own the token rather than every
app minting its own.

    ams = AmsClient()
    ams.call("insert_task_tool", {"subject": "Call the insured", ...})

THE GOTCHA THIS EXISTS TO ABSORB: MCP-over-HTTP always answers **HTTP 200**.
Failures — including auth failures — come back inside the JSON-RPC body as an
`error` object. Code that checks the status code sees success and carries on
with nothing. `call()` raises on the body, so a caller cannot mistake a refusal
for an empty result. That distinction matters most where an empty list is a
plausible answer: "no carriers for this risk" reads as a declination, and "no
tasks" reads as nothing to do.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 60.0  # a NowCerts password grant alone can take ~26s


def _parse_body(raw: str) -> dict[str, Any] | None:
    """The JSON-RPC body, whether it arrived as JSON or in SSE frames.

    A streamable-HTTP MCP server answers the same request with either shape
    depending on what it negotiates. The Hermes bridge replies in SSE:

        event: message
        data: {"jsonrpc":"2.0","id":1,"result":{...}}

    Handing that to json.loads fails, so a client that only speaks JSON cannot
    talk to it at all — which is how a door serving 30 tools reported zero.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        pass
    # SSE: take the last data: frame carrying a JSON-RPC body. Last, not first —
    # a server may send progress notifications before the result.
    body = None
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        chunk = line[len("data:"):].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            parsed = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
            body = parsed
    return body


def _text_of(result: dict[str, Any]) -> str:
    """The human-readable part of an MCP result, for error messages."""
    parts = [
        c.get("text", "")
        for c in (result.get("content") or [])
        if isinstance(c, dict) and c.get("text")
    ]
    return " ".join(parts).strip() or str(result)[:200]


class AmsError(RuntimeError):
    """A NowCerts MCP call failed, or was refused."""


class AmsClient:
    """Calls NowCerts MCP tools through the Hermes door.

    ``base_url`` defaults to HERMES_AMS_MCP_URL, then the loopback NowCerts
    door on the box. ``token`` defaults to HERMES_AMS_MCP_TOKEN, then the shared
    API_SERVER_KEY the rest of the estate uses.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("HERMES_AMS_MCP_URL")
            or "http://127.0.0.1:8791/mcp"
        ).rstrip("/")
        self.token = token or os.environ.get("HERMES_AMS_MCP_TOKEN") or os.environ.get(
            "API_SERVER_KEY"
        )
        self.timeout = timeout if timeout is not None else float(
            os.environ.get("HERMES_AMS_MCP_TIMEOUT", DEFAULT_TIMEOUT)
        )
        self._id = 0

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        # Both types, or a streamable-HTTP MCP server refuses the request outright:
        # "Not Acceptable: Client must accept both application/json and
        # text/event-stream". The intake gate does exactly this. Sending only JSON
        # made every call to it fail before it reached a tool.
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            self.base_url, data=json.dumps(payload).encode("utf-8"),
            method="POST", headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - transport only
            raise AmsError(f"{method}: HTTP {exc.code} from the AMS door") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - transport only
            raise AmsError(f"{method}: cannot reach the AMS door at {self.base_url}") from exc

        body = _parse_body(raw)
        if body is None:
            raise AmsError(f"{method}: AMS door returned non-JSON: {raw[:200]}")

        # The whole reason this wrapper exists. A 200 means the request was
        # delivered, not that it worked.
        if isinstance(body, dict) and body.get("error"):
            err = body["error"]
            code = err.get("code") if isinstance(err, dict) else None
            msg = err.get("message") if isinstance(err, dict) else err
            if code == -32001:
                raise AmsError(
                    f"{method}: refused by the AMS door (unauthorized). The bearer is "
                    "missing or wrong — this is NOT an empty result."
                )
            raise AmsError(f"{method}: AMS door returned error {code}: {msg}")

        result = body.get("result") if isinstance(body, dict) else body

        # The THIRD failure shape, and the sneakiest. A tool can fail without any
        # JSON-RPC error at all: the envelope succeeds and the result carries
        # isError=true with the reason in its content. Asking the live door for a
        # tool it does not have returns exactly this —
        #   {"result": {"content": [{"text": "Unknown tool: x"}], "isError": true}}
        # so a caller that only checks the status code AND the error object still
        # sees success, and reads the failure text as data.
        if isinstance(result, dict) and result.get("isError"):
            raise AmsError(f"{method}: tool reported failure: {_text_of(result)}")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """Every tool the AMS door exposes. Useful for checking a tool name
        before wiring it, rather than discovering the typo as an empty result."""
        result = self._rpc("tools/list") or {}
        return result.get("tools", []) if isinstance(result, dict) else []

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke one NowCerts MCP tool by name.

        Raises AmsError on refusal or failure — never returns an empty result to
        stand in for one.
        """
        if not tool:
            raise ValueError("tool name is required")
        return self._rpc("tools/call", {"name": tool, "arguments": arguments or {}})
