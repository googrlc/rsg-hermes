"""Momentum MCP client for Renewal Loop v6 AMS writeback."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import requests

from . import config


class MomentumMCPClientError(Exception):
    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class MomentumMCPClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("MOMENTUM_MCP_URL") or config.MOMENTUM_MCP_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("MOMENTUM_MCP_API_KEY") or config.MOMENTUM_MCP_API_KEY
        self.timeout = timeout
        self.session = session or requests.Session()
        if not self.base_url or not self.api_key:
            raise MomentumMCPClientError(
                "MOMENTUM_MCP_URL and MOMENTUM_MCP_API_KEY must be set.",
                retryable=False,
            )

    def manage_notes(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call_tool(config.MOMENTUM_MCP_TOOL_NOTES, payload)

    def _call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": payload},
        }
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        try:
            resp = self.session.post(
                self.base_url,
                headers=headers,
                json=body,
                timeout=self.timeout,
                stream=True,
            )
        except requests.RequestException as exc:
            raise MomentumMCPClientError(str(exc), retryable=True) from exc

        if not resp.ok:
            raise MomentumMCPClientError(
                f"{resp.status_code} {getattr(resp, 'text', '')}".strip(),
                retryable=resp.status_code >= 500,
                status_code=resp.status_code,
            )

        result = self._decode_response(resp)
        if isinstance(result, dict) and result.get("error"):
            error = result["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            code = error.get("code") if isinstance(error, dict) else None
            retryable = False if isinstance(code, int) and 400 <= code < 500 else True
            raise MomentumMCPClientError(message or "Momentum MCP error", retryable=retryable)
        if isinstance(result, dict) and "result" in result and isinstance(result["result"], dict):
            return result["result"]
        if isinstance(result, dict):
            return result
        return {"result": result}

    def _decode_response(self, resp: Any) -> dict[str, Any] | list[Any] | str:
        content_type = (getattr(resp, "headers", {}) or {}).get("content-type", "").lower()
        if "text/event-stream" in content_type:
            return self._decode_sse(resp)
        try:
            return resp.json()
        except Exception as exc:
            raise MomentumMCPClientError(f"Invalid Momentum MCP response: {exc}", retryable=True) from exc

    @staticmethod
    def _decode_sse(resp: Any) -> dict[str, Any] | list[Any] | str:
        last_data: Any = None
        for raw_line in resp.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            line = raw_line.decode() if isinstance(raw_line, bytes) else str(raw_line)
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                last_data = json.loads(payload)
            except json.JSONDecodeError:
                last_data = payload
        if last_data is None:
            raise MomentumMCPClientError("Empty SSE response from Momentum MCP.", retryable=True)
        return last_data
