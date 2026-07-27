"""Thin Supabase PostgREST wrapper for Hermes operations and CRM dual-writes."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests
from requests.adapters import HTTPAdapter, Retry

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
        pool_connections: int = 10,
        pool_maxsize: int = 20,
        max_retries: int = 3,
    ) -> None:
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or os.environ.get("SUPABASE_KEY", "")
        self.timeout = timeout
        if not self.url or not self.key:
            raise SupabaseClientError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY) must be set (env or constructor)."
            )

        # Connection pooling with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=0.1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "PUT", "DELETE", "PATCH"],
        )
        adapter = HTTPAdapter(
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
            max_retries=retry_strategy,
            pool_block=False,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _headers(self, *, prefer: str = "return=representation") -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": prefer,
        }

    def insert(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        """INSERT a single row; returns the created record."""
        resp = self.session.post(
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
        resp = self.session.get(
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
        resp = self.session.post(
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

    def update(self, table: str, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """PATCH a single row by primary key."""
        resp = self.session.patch(
            f"{self.url}/rest/v1/{table}",
            headers=self._headers(),
            json=payload,
            params={"id": f"eq.{record_id}"},
            timeout=self.timeout,
        )
        if not resp.ok:
            log.error("Supabase update %s failed: %s %s", table, resp.status_code, resp.text[:500])
            raise SupabaseClientError(f"{resp.status_code} UPDATE {table}: {resp.text[:500]}")
        rows = resp.json()
        return rows[0] if isinstance(rows, list) and rows else rows

    def upsert(
        self,
        table: str,
        payload: dict[str, Any],
        *,
        on_conflict: str = "id",
    ) -> dict[str, Any]:
        """INSERT … ON CONFLICT via PostgREST ``Prefer: resolution=merge-duplicates``."""
        headers = self._headers(prefer="return=representation,resolution=merge-duplicates")
        resp = self.session.post(
            f"{self.url}/rest/v1/{table}",
            headers=headers,
            json=payload,
            params={"on_conflict": on_conflict},
            timeout=self.timeout,
        )
        if not resp.ok:
            log.error("Supabase upsert %s failed: %s %s", table, resp.status_code, resp.text[:500])
            raise SupabaseClientError(f"{resp.status_code} UPSERT {table}: {resp.text[:500]}")
        rows = resp.json()
        return rows[0] if isinstance(rows, list) and rows else rows

    def update_where(
        self,
        table: str,
        payload: dict[str, Any],
        *,
        filters: dict[str, str],
    ) -> list[dict[str, Any]]:
        """PATCH rows matching arbitrary PostgREST filter params.

        Example: ``supa.update_where("canonical_clients", {"active": False},
                                     filters={"nowcerts_insured_guid": "eq.nc-1"})``
        """
        resp = self.session.patch(
            f"{self.url}/rest/v1/{table}",
            headers=self._headers(),
            json=payload,
            params=filters,
            timeout=self.timeout,
        )
        if not resp.ok:
            log.error("Supabase update_where %s failed: %s %s", table, resp.status_code, resp.text[:500])
            raise SupabaseClientError(f"{resp.status_code} UPDATE {table}: {resp.text[:500]}")
        rows = resp.json()
        return rows if isinstance(rows, list) else [rows] if rows else []

    def delete_where(self, table: str, *, filters: dict[str, str]) -> None:
        """DELETE rows matching arbitrary PostgREST filter params.

        The by-primary-key ``delete`` cannot clear a case's children, and relying
        on ON DELETE CASCADE would mean trusting a constraint this repo does not
        own — agency_crm_case_events and agency_crm_document_links are created by
        the shared schema.
        """
        if not filters:
            raise ValueError("delete_where needs a filter; refusing to delete a whole table")
        resp = self.session.delete(
            f"{self.url}/rest/v1/{table}",
            headers=self._headers(prefer=""),
            params=filters,
            timeout=self.timeout,
        )
        if not resp.ok:
            log.error("Supabase delete_where %s failed: %s %s", table, resp.status_code, resp.text[:500])
            raise SupabaseClientError(f"{resp.status_code} DELETE {table}: {resp.text[:500]}")

    def delete(self, table: str, record_id: str) -> None:
        """DELETE a single row by primary key."""
        resp = self.session.delete(
            f"{self.url}/rest/v1/{table}",
            headers=self._headers(prefer=""),
            params={"id": f"eq.{record_id}"},
            timeout=self.timeout,
        )
        if not resp.ok:
            log.error("Supabase delete %s failed: %s %s", table, resp.status_code, resp.text[:500])
            raise SupabaseClientError(f"{resp.status_code} DELETE {table}: {resp.text[:500]}")

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
