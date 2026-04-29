"""Thin wrapper around EspoCRM REST API v1 (X-Api-Key auth)."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urljoin

import requests


class EspoClientError(Exception):
    """Raised when the API returns a non-success status or the response is invalid."""


class EspoClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        raw = (base_url or os.environ.get("ESPO_URL", "")).rstrip("/")
        self.base_url = raw if raw.endswith("/api/v1") else f"{raw}/api/v1"
        self.api_key = api_key or os.environ.get("ESPO_API_KEY", "")
        self.timeout = timeout
        if not self.base_url or not self.api_key:
            raise EspoClientError("ESPO_URL and ESPO_API_KEY must be set (env or constructor).")

    def _headers(self) -> dict[str, str]:
        return {
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _query_params(params: dict[str, Any] | None) -> dict[str, str] | None:
        """EspoCRM expects JSON-encoded complex GET params (where, orderBy, select)."""
        if not params:
            return None
        flat: dict[str, str] = {}
        for key, val in params.items():
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
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        resp = requests.request(
            method.upper(),
            url,
            headers=self._headers(),
            params=self._query_params(params),
            json=json,
            timeout=self.timeout,
        )
        try:
            body = resp.json() if resp.content else {}
        except ValueError as e:
            raise EspoClientError(f"Invalid JSON from {url}: {resp.text[:500]}") from e
        if not resp.ok:
            raise EspoClientError(f"{resp.status_code} {method} {path}: {body}")
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
