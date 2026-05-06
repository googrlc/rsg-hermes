"""Thin Supabase PostgREST wrapper for dual-writing CRM events."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)


class SupabaseClientError(Exception):
    """Raised on non-success responses from Supabase."""


class SupabaseClient:
    """REST-only client targeting the PostgREST API on a Supabase project."""

    def __init__(
        self,
        url: str | None = None,
        key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.environ.get("SUPABASE_KEY", "")
        self.timeout = timeout
        if not self.url or not self.key:
            raise SupabaseClientError(
                "SUPABASE_URL and SUPABASE_KEY must be set (env or constructor)."
            )

    def _headers(self, *, prefer: str = "return=representation") -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": prefer,
        }

    def insert(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        """INSERT a single row; returns the created record."""
        resp = requests.post(
            f"{self.url}/rest/v1/{table}",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        if not resp.ok:
            log.error("Supabase insert %s failed: %s %s", table, resp.status_code, resp.text[:500])
            raise SupabaseClientError(f"{resp.status_code} INSERT {table}: {resp.text[:500]}")
        rows = resp.json()
        return rows[0] if isinstance(rows, list) and rows else rows

    def select(
        self,
        table: str,
        *,
        columns: str = "*",
        params: dict[str, str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """SELECT rows with optional PostgREST query params."""
        query: dict[str, str] = {"select": columns, "limit": str(limit)}
        if params:
            query.update(params)
        resp = requests.get(
            f"{self.url}/rest/v1/{table}",
            headers=self._headers(prefer=""),
            params=query,
            timeout=self.timeout,
        )
        if not resp.ok:
            log.error("Supabase select %s failed: %s %s", table, resp.status_code, resp.text[:500])
            raise SupabaseClientError(f"{resp.status_code} SELECT {table}: {resp.text[:500]}")
        body = resp.json()
        return body if isinstance(body, list) else []

    def rpc(self, name: str, payload: dict[str, Any]) -> list[dict[str, Any]] | dict[str, Any]:
        """Call a Supabase PostgREST RPC."""
        resp = requests.post(
            f"{self.url}/rest/v1/rpc/{name}",
            headers=self._headers(prefer=""),
            json=payload,
            timeout=self.timeout,
        )
        if not resp.ok:
            log.error("Supabase rpc %s failed: %s %s", name, resp.status_code, resp.text[:500])
            raise SupabaseClientError(f"{resp.status_code} RPC {name}: {resp.text[:500]}")
        body = resp.json() if resp.content else {}
        return body

    def log_slack_intake(
        self,
        *,
        channel_id: str,
        user_id: str | None,
        message_text: str,
        message_ts: str | None = None,
        parsed_command: str | None = None,
    ) -> dict[str, Any]:
        """Convenience: write to stg_slack_intake_notes."""
        return self.insert(
            "stg_slack_intake_notes",
            {
                "channel_id": channel_id,
                "user_id": user_id,
                "message_text": message_text,
                "message_ts": message_ts,
                "parsed_command": parsed_command,
                "source_record_id": message_ts,
                "payload": {},
            },
        )

    def log_lead(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Convenience: write to leads_staging."""
        return self.insert("leads_staging", payload)
