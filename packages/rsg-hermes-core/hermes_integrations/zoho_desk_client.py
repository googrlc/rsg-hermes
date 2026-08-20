"""Zoho Desk REST client: OAuth2 refresh-token auth, retry, singleton.

Desk is the case/workflow layer. This client talks to Desk APIs for Tickets,
Contacts, and Accounts. It does not create departments, layouts, or Blueprints
— those are configured in the Desk admin UI from ``docs/zoho-desk/``.

Auth reuses the Zoho CRM OAuth client when the refresh token has Desk scopes,
or ``ZOHO_DESK_*`` overrides when Desk is a separate client.

Environment:
  ZOHO_CLIENT_ID            or ZOHO_DESK_CLIENT_ID
  ZOHO_CLIENT_SECRET        or ZOHO_DESK_CLIENT_SECRET
  ZOHO_REFRESH_TOKEN        or ZOHO_DESK_REFRESH_TOKEN
  ZOHO_DESK_ORG_ID          optional; listed from GET /organizations when omitted
  ZOHO_DATA_CENTER          default com  (desk.zoho.{dc})
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 4
_RETRY_BACKOFF_SECONDS = 2.0
_TOKEN_EXPIRY_SKEW_SECONDS = 120

_shared: "ZohoDeskClient | None" = None
_shared_lock = threading.Lock()


class ZohoDeskClientError(Exception):
    """Raised on auth failures or non-success Desk API responses."""


def _env_float(name: str, fallback: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return fallback


def _first_env(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def get_client() -> "ZohoDeskClient":
    """Process-wide Desk client — prefer this over ``ZohoDeskClient()``."""
    global _shared
    if _shared is None:
        with _shared_lock:
            if _shared is None:
                _shared = ZohoDeskClient()
    return _shared


def reset_client() -> None:
    """Drop the singleton (tests / credential rotation)."""
    global _shared
    with _shared_lock:
        _shared = None


class ZohoDeskClient:
    """Thin REST client for Zoho Desk v1.

    Auth: POST accounts.zoho.{dc}/oauth/v2/token with grant_type=refresh_token.
    API:  https://desk.zoho.{dc}/api/v1/
    """

    def __init__(
        self,
        *,
        org_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        data_center: str | None = None,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> None:
        self.org_id = org_id or _first_env("ZOHO_DESK_ORG_ID")
        self.client_id = client_id or _first_env("ZOHO_DESK_CLIENT_ID", "ZOHO_CLIENT_ID")
        self.client_secret = client_secret or _first_env(
            "ZOHO_DESK_CLIENT_SECRET", "ZOHO_CLIENT_SECRET"
        )
        self.refresh_token = refresh_token or _first_env(
            "ZOHO_DESK_REFRESH_TOKEN", "ZOHO_REFRESH_TOKEN"
        )
        dc = (data_center or os.environ.get("ZOHO_DATA_CENTER", "com") or "com").strip().lstrip(".")
        self.data_center = dc or "com"
        self.accounts_base = f"https://accounts.zoho.{self.data_center}"
        self.api_base = f"https://desk.zoho.{self.data_center}/api/v1"
        self.timeout = (
            timeout if timeout is not None else _env_float("ZOHO_DESK_TIMEOUT", DEFAULT_TIMEOUT)
        )
        self.retries = (
            retries if retries is not None else int(_env_float("ZOHO_DESK_RETRIES", DEFAULT_RETRIES))
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._auth_lock = threading.Lock()
        if not self.client_id or not self.client_secret or not self.refresh_token:
            raise ZohoDeskClientError(
                "ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN "
                "(or ZOHO_DESK_* overrides) must be set."
            )

    def _authenticate(self) -> str:
        url = f"{self.accounts_base}/oauth/v2/token"
        resp = requests.post(
            url,
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        if not resp.ok:
            raise ZohoDeskClientError(
                f"Zoho Desk auth failed {resp.status_code}: {resp.text[:500]}"
            )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise ZohoDeskClientError(
                f"Zoho Desk auth response missing access_token: {str(body)[:300]}"
            )
        expires_in = int(body.get("expires_in") or 3600)
        self._token = token
        self._token_expires_at = time.time() + max(60, expires_in - _TOKEN_EXPIRY_SKEW_SECONDS)
        log.info("Zoho Desk: authenticated (dc=%s)", self.data_center)
        return token

    def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        with self._auth_lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token
            return self._authenticate()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Zoho-oauthtoken {self._ensure_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.org_id:
            headers["orgId"] = self.org_id
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.api_base}/{path.lstrip('/')}"
        last: Exception | None = None
        attempts = max(1, self.retries + 1)
        for attempt in range(attempts):
            try:
                resp = requests.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last = exc
                if attempt + 1 < attempts:
                    delay = _RETRY_BACKOFF_SECONDS * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise ZohoDeskClientError(
                    f"Zoho Desk {method} {path}: {attempts} attempts all timed out"
                ) from last

            if resp.status_code == 401:
                with self._auth_lock:
                    self._authenticate()
                if attempt + 1 < attempts:
                    continue
                raise ZohoDeskClientError(
                    f"Zoho Desk {method} {path} failed 401 after re-auth: {resp.text[:500]}"
                )
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else _RETRY_BACKOFF_SECONDS * (2 ** attempt)
                except ValueError:
                    delay = _RETRY_BACKOFF_SECONDS * (2 ** attempt)
                if attempt + 1 < attempts:
                    time.sleep(delay)
                    continue
                raise ZohoDeskClientError(
                    f"Zoho Desk {method} {path} rate limited after {attempts} attempts"
                )
            if resp.status_code == 204:
                return {}
            if not resp.ok:
                raise ZohoDeskClientError(
                    f"Zoho Desk {method} {path} failed {resp.status_code}: {resp.text[:500]}"
                )
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError as exc:
                raise ZohoDeskClientError(
                    f"Zoho Desk {method} {path}: non-JSON response: {resp.text[:300]}"
                ) from exc
        raise ZohoDeskClientError(f"Zoho Desk {method} {path}: retry budget exhausted") from last

    @staticmethod
    def _rows(body: Any) -> list[dict[str, Any]]:
        if isinstance(body, list):
            return [row for row in body if isinstance(row, dict)]
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
            if body.get("id") or body.get("name") or body.get("displayLabel"):
                return [body]
        return []

    def list_organizations(self) -> list[dict[str, Any]]:
        return self._rows(self._request("GET", "organizations"))

    def list_agents(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._rows(self._request("GET", "agents", params={"limit": limit}))

    def list_departments(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._rows(self._request("GET", "departments", params={"limit": limit, "isEnabled": True}))

    def create_department(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._request("POST", "departments", json_body=payload)
        return body if isinstance(body, dict) else {}

    def list_organization_fields(self, module: str = "tickets") -> list[dict[str, Any]]:
        return self._rows(self._request("GET", "organizationFields", params={"module": module}))

    def create_field(self, payload: dict[str, Any], *, module: str = "tickets") -> dict[str, Any]:
        body = self._request("POST", "fields", json_body=payload, params={"module": module})
        return body if isinstance(body, dict) else {}

    def list_teams(self, *, department_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if department_id:
            params["departmentId"] = department_id
        return self._rows(self._request("GET", "teams", params=params))

    def create_team(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._request("POST", "teams", json_body=payload)
        return body if isinstance(body, dict) else {}

    def list_tickets(self, *, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        body = self._request("GET", "tickets", params=params)
        data = body.get("data") if isinstance(body, dict) else None
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        body = self._request("GET", f"tickets/{ticket_id}")
        return body if isinstance(body, dict) else {}

    def create_ticket(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._request("POST", "tickets", json_body=payload)
        return body if isinstance(body, dict) else {}

    def update_ticket(self, ticket_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._request("PATCH", f"tickets/{ticket_id}", json_body=payload)
        return body if isinstance(body, dict) else {}

    def search_tickets(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        body = self._request("GET", "tickets/search", params={"limit": limit, "q": query})
        data = body.get("data") if isinstance(body, dict) else None
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
