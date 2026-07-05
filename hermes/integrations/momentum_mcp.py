"""Momentum AMS MCP client (Streamable HTTP) — read-only foundation for RSG agents.

Wraps the hosted Momentum MCP endpoint (https://mcp.momentumamp.com/mcp) using the
Model Context Protocol over Streamable HTTP (SSE). Handles the initialize ->
notifications/initialized -> tools/call handshake, session re-establishment, and
read-side convenience methods for the entities the RSG agents need.

Write methods, blast-radius caps, Supabase audit mirroring, and dry-run gating are
intentionally NOT enabled here yet; they land when Agent 01 promotes past read-only.
See 00_shared_standards.md sections 3.1-3.6.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

DEFAULT_URL = "https://mcp.momentumamp.com/mcp"
PROTOCOL_VERSION = "2024-11-05"


class MomentumMCPError(Exception):
    """Raised on auth failures, non-success responses, or MCP-level errors."""


class MomentumMCPClient:
    """Read-only MCP client for Momentum AMS.

    Config via env: ``MOMENTUM_MCP_URL`` (default below), ``MCP_API_KEY`` (Bearer token).
    """

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
        client_name: str = "rsg-hermes",
        client_version: str = "0.1.0",
    ) -> None:
        self.url = (url or os.environ.get("MOMENTUM_MCP_URL", DEFAULT_URL)).rstrip("/")
        self.api_key = api_key or os.environ.get("MCP_API_KEY", "")
        if not self.api_key:
            raise MomentumMCPError("MCP_API_KEY must be set (env or constructor).")
        self.timeout = timeout
        self._client_info = {"name": client_name, "version": client_version}
        self._session_id: str | None = None
        self._call_id = 100

    # -- low-level transport -------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.api_key}",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    @staticmethod
    def _parse_sse(content_bytes: bytes) -> dict[str, Any]:
        text = content_bytes.decode("utf-8", "replace")
        data = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
        if not data:
            return {"_raw": text[:500]}
        try:
            return json.loads("\n".join(data))
        except json.JSONDecodeError as e:
            return {"_raw": text[:500], "_parse_error": str(e)}

    def _next_id(self) -> int:
        self._call_id += 1
        return self._call_id

    def _ensure_session(self) -> None:
        if self._session_id:
            return
        headers = {k: v for k, v in self._headers().items() if k != "Mcp-Session-Id"}
        resp = requests.post(
            self.url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": self._client_info,
                },
            },
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise MomentumMCPError(f"initialize HTTP {resp.status_code}: {resp.text[:200]}")
        sid = resp.headers.get("Mcp-Session-Id")
        if not sid:
            raise MomentumMCPError("initialize ok but no Mcp-Session-Id returned.")
        self._session_id = sid
        # notifications/initialized has no id; server returns 202 with empty body.
        requests.post(
            self.url,
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=self.timeout,
        )
        log.info("Momentum MCP session established: %s", sid)

    def _call(self, method: str, params: dict[str, Any] | None = None, retry: bool = True) -> dict[str, Any]:
        self._ensure_session()
        result = self._post({"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params or {}})
        if "error" in result and retry and self._is_session_error(result):
            log.warning("MCP session error, re-establishing: %s", result["error"])
            self._session_id = None
            return self._call(method, params, retry=False)
        return result

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = requests.post(self.url, headers=self._headers(), json=payload, timeout=self.timeout)
        if resp.status_code >= 400:
            raise MomentumMCPError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return self._parse_sse(resp.content)

    @staticmethod
    def _is_session_error(result: dict[str, Any]) -> bool:
        err = result.get("error") or {}
        code = err.get("code")
        msg = str(err.get("message", "")).lower()
        return code in (-32000, -32600) or "session" in msg or "unauthorized" in msg

    # -- public API ----------------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        res = self._call("tools/list")
        return res.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call an MCP tool; return parsed JSON from the text content block."""
        res = self._call("tools/call", {"name": name, "arguments": arguments or {}})
        if "error" in res:
            raise MomentumMCPError(f"tool '{name}' error: {json.dumps(res['error'])[:300]}")
        for block in res.get("result", {}).get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except json.JSONDecodeError:
                    return block["text"]
        return res.get("result", {}).get("content", [])

    # -- read conveniences (Agent 01 + 02 oriented) --------------------------
    def list_policies(
        self, *, active: bool | None = None, top: int = 100, skip: int = 0,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"top": top, "skip": skip}
        if active is not None:
            args["active"] = active
        if filters:
            args.update(filters)
        return self.call_tool("get_policy_list_tool", args)

    def list_all_policies(self, *, active: bool | None = None, page_size: int = 100, pause: float = 0.3) -> list[dict[str, Any]]:
        """Paginate the full policy book. ``active=None`` walks both active and inactive."""
        if active is None:
            out: list[dict[str, Any]] = []
            for a in (True, False):
                out.extend(self.list_all_policies(active=a, page_size=page_size, pause=pause))
            return out
        out: list[dict[str, Any]] = []
        skip = 0
        while True:
            page = self.list_policies(active=active, top=page_size, skip=skip)
            lst = page.get("policy_list", []) or []
            out.extend(lst)
            total = page.get("total_count", 0)
            skip += page_size
            if pause:
                time.sleep(pause)
            if skip >= total or not lst:
                break
        return out

    def get_insured(self, insured_database_id: str) -> dict[str, Any]:
        return self.call_tool("get_insured_detail_list_tool", {"insured_database_id": insured_database_id})

    def search_insureds(self, search_text: str | None = None, **extra: Any) -> Any:
        args = {k: v for k, v in {"searchText": search_text, **extra}.items() if v is not None}
        return self.call_tool("get_insured_details_tool", args)

    def list_notes(self, insured_database_id: str | None = None, **extra: Any) -> Any:
        args = {k: v for k, v in {"insured_database_id": insured_database_id, **extra}.items() if v is not None}
        return self.call_tool("get_notes_list_tool", args)

    def get_expiring_policies(self, minimum_expiry: str | None = None, maximum_expiry: str | None = None, is_quote: bool = False) -> Any:
        args: dict[str, Any] = {"is_quote": is_quote}
        if minimum_expiry:
            args["minimum_policy_expiry_date"] = minimum_expiry
        if maximum_expiry:
            args["maximum_policy_expiry_date"] = maximum_expiry
        return self.call_tool("get_expiring_policy_quote_list_tool", args)

    # -- write surface (gated; not enabled in read-only phase) ---------------
    # apply_insured_tag_tool / insert_note_tool / insert_opportunity_tool /
    # insert_task_tool / apply_policy_tag_tool + blast-radius caps (50/run,
    # 1 write/sec), Supabase agent_writes mirror, and dry_run gating land here
    # when Agent 01 promotes dry_run -> shadow. Per shared standard 3.2/3.3.

    def close(self) -> None:
        self._session_id = None
