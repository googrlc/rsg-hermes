"""Thin wrapper around EspoCRM REST API v1 (X-Api-Key auth)."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urljoin

import requests
from requests import Session
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)


class EspoClientError(Exception):
    """Raised when the API returns a non-success status or the response is invalid."""


class EspoClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
        verify: bool | None = None,
        session: Session | None = None,
        max_retries: int | None = None,
        retry_sleep: float | None = None,
        max_list_size: int | None = None,
    ) -> None:
        raw = (base_url or os.environ.get("ESPO_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("ESPO_API_KEY", "")
        if not raw or not self.api_key:
            raise EspoClientError("ESPO_URL and ESPO_API_KEY must be set (env or constructor).")
        self.base_url = raw if raw.endswith("/api/v1") else f"{raw}/api/v1"
        self.timeout = timeout
        self.verify = (
            verify
            if verify is not None
            else os.environ.get("HERMES_VERIFY_TLS", "").strip().lower() in ("1", "true", "yes")
        )
        self.session = session or requests.Session()
        self.max_retries = (
            max_retries
            if max_retries is not None
            else int(os.environ.get("HERMES_READ_RETRIES", "2"))
        )
        self.retry_sleep = (
            retry_sleep
            if retry_sleep is not None
            else float(os.environ.get("HERMES_RETRY_SLEEP", "0.5"))
        )
        self.max_list_size = (
            max_list_size
            if max_list_size is not None
            else int(os.environ.get("HERMES_MAX_LIST_SIZE", "200"))
        )

    def _headers(self) -> dict[str, str]:
        return {
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _query_params(self, params: dict[str, Any] | None) -> dict[str, str] | None:
        """EspoCRM expects JSON-encoded complex GET params (where, orderBy, select)."""
        if not params:
            return None
        flat: dict[str, str] = {}
        for key, val in params.items():
            if key == "maxSize":
                try:
                    val = min(int(val), self.max_list_size)
                except (TypeError, ValueError):
                    pass
            if isinstance(val, (list, dict)):
                flat[key] = json.dumps(val)
            else:
                flat[key] = str(val)
        return flat

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        method_upper = method.upper()
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        last_error: EspoClientError | None = None
        attempts = self.max_retries + 1 if self._is_retryable_method(method_upper) else 1
        for attempt in range(1, attempts + 1):
            try:
                resp = self.session.request(
                    method=method_upper,
                    url=url,
                    headers=self._headers(),
                    params=self._query_params(params),
                    json=json,
                    timeout=self.timeout,
                    verify=self.verify,
                )
            except requests.RequestException as e:
                last_error = EspoClientError(
                    f"Network error {method_upper} {path}: {e}. "
                    "Check EspoCRM reachability, DNS/Tailscale/VPS networking, and ESPO_URL."
                )
                if attempt < attempts:
                    self._sleep_before_retry()
                    continue
                raise last_error from e

            if self._should_retry_status(resp.status_code) and attempt < attempts:
                self._sleep_before_retry()
                continue
            return self._parse_response(resp, method_upper, path, url)

        if last_error:
            raise last_error
        raise EspoClientError(f"Request failed without a response: {method_upper} {path}")

    @staticmethod
    def _is_retryable_method(method: str) -> bool:
        """Retry reads, not writes, to avoid duplicate CRM data entry."""
        return method in {"GET", "HEAD", "OPTIONS"}

    @staticmethod
    def _should_retry_status(status_code: int) -> bool:
        return status_code in {429, 500, 502, 503, 504}

    def _sleep_before_retry(self) -> None:
        if self.retry_sleep > 0:
            time.sleep(self.retry_sleep)

    @staticmethod
    def _status_hint(status_code: int) -> str:
        if status_code == 401:
            return "Authentication failed: check ESPO_API_KEY and the EspoCRM API user."
        if status_code == 403:
            return (
                "EspoCRM API user lacks permission for this entity/action. "
                "Fix the API user's Role/Team, then verify with `hermes --doctor` or `hermes --kpi`."
            )
        if status_code == 404:
            return "Endpoint or entity not found: verify the entity name and EspoCRM route."
        if status_code == 422:
            return "EspoCRM rejected the payload: verify field names, enum values, and required fields."
        if status_code in {429, 500, 502, 503, 504}:
            return "Transient EspoCRM/server failure after retries."
        return "EspoCRM request failed."

    def _parse_response(
        self,
        resp: requests.Response,
        method: str,
        path: str,
        url: str,
    ) -> dict[str, Any] | list[Any]:
        try:
            body = resp.json() if resp.content else {}
        except ValueError as e:
            raise EspoClientError(f"Invalid JSON from {url}: {resp.text[:500]}") from e
        if not resp.ok:
            raise EspoClientError(f"{resp.status_code} {method} {path}: {self._status_hint(resp.status_code)} Body: {body}")
        return body

    def get(self, path: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        return self.request("GET", path, params=kwargs.get("params"))

    def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        return self.request("POST", path, json=json)

    def put(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        return self.request("PUT", path, json=json)

    def patch(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        return self.request("PATCH", path, json=json)

    def delete(self, path: str) -> dict[str, Any] | list[Any]:
        return self.request("DELETE", path)

    def ping(self) -> dict[str, Any] | list[Any]:
        """Verify credentials against the current user endpoint."""
        return self.get("App/user")

    def get_metadata(self, key: str | None = None) -> dict[str, Any] | list[Any] | Any:
        """Fetch Espo metadata, optionally returning a top-level metadata key."""
        if not key:
            return self.get("Metadata")
        try:
            return self.get(f"Metadata/{key}")
        except EspoClientError:
            metadata = self.get("Metadata")
            if isinstance(metadata, dict) and key in metadata:
                return metadata[key]
            raise

    def search(
        self,
        entity: str,
        query: str,
        *,
        max_size: int = 10,
        select: str | None = None,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search an entity by name-like fields."""
        selected = select or "id,name"
        where_fields = fields or ["name"]
        body = self.get(
            entity,
            params={
                "maxSize": max_size,
                "select": selected,
                "where": [
                    {
                        "type": "or",
                        "value": [
                            {"type": "contains", "attribute": field, "value": query}
                            for field in where_fields
                        ],
                    }
                ],
            },
        )
        if isinstance(body, dict) and isinstance(body.get("list"), list):
            return [row for row in body["list"] if isinstance(row, dict)]
        return []

    def create(self, entity: str, payload: dict[str, Any]) -> dict[str, Any] | list[Any]:
        return self.post(entity, json=payload)

    def update(self, entity: str, record_id: str, payload: dict[str, Any]) -> dict[str, Any] | list[Any]:
        return self.put(f"{entity}/{record_id}", json=payload)

    def find_one_by_field(
        self,
        entity: str,
        field: str,
        value: str,
        *,
        select: str = "id,name",
    ) -> dict[str, Any] | None:
        body = self.get(
            entity,
            params={
                "maxSize": 1,
                "select": select,
                "where": [{"type": "equals", "attribute": field, "value": value}],
            },
        )
        if isinstance(body, dict) and isinstance(body.get("list"), list) and body["list"]:
            row = body["list"][0]
            return row if isinstance(row, dict) else None
        return None

    def upsert_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create or update a Contact, preferring email as the match key."""
        email = str(payload.get("emailAddress") or payload.get("email") or "").strip()
        if email and "emailAddress" not in payload:
            payload = {**payload, "emailAddress": email}
        existing = None
        if email:
            existing = self.find_one_by_field(
                "Contact",
                "emailAddress",
                email,
                select="id,name,emailAddress",
            )
        if not existing and payload.get("name"):
            hits = self.search("Contact", str(payload["name"]), max_size=1, select="id,name,emailAddress")
            existing = hits[0] if hits else None
        if existing and existing.get("id"):
            updated = self.update("Contact", str(existing["id"]), payload)
            return updated if isinstance(updated, dict) else {"id": existing["id"], "result": updated}
        created = self.create("Contact", payload)
        return created if isinstance(created, dict) else {"result": created}

    def upsert_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create or update an Account, preferring FEIN and then account name."""
        fein_attr = os.environ.get("HERMES_ACCOUNT_FEIN_ATTR", "fein")
        fein = str(payload.get(fein_attr) or payload.get("fein") or "").strip()
        existing = None
        if fein:
            try:
                existing = self.find_one_by_field(
                    "Account",
                    fein_attr,
                    fein,
                    select=f"id,name,{fein_attr}",
                )
            except EspoClientError:
                existing = None
        if not existing and payload.get("name"):
            hits = self.search("Account", str(payload["name"]), max_size=1, select="id,name")
            existing = hits[0] if hits else None
        if existing and existing.get("id"):
            updated = self.update("Account", str(existing["id"]), payload)
            return updated if isinstance(updated, dict) else {"id": existing["id"], "result": updated}
        created = self.create("Account", payload)
        return created if isinstance(created, dict) else {"result": created}
