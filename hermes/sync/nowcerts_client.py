"""NowCerts API client: bearer-token auth, OData pagination."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)


class NowCertsClientError(Exception):
    """Raised on auth failures or non-success API responses."""


class NowCertsClient:
    """Thin REST client for the NowCerts AMS API.

    Auth: POST /api/token with grant_type=password to get a bearer token.
    Pagination: OData — $count=true, $orderby, $skip, $top.
    """

    TOKEN_ENDPOINT = "/api/token"

    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("NOWCERTS_API_URL", "https://api.nowcerts.com")
        ).rstrip("/")
        self._username = username or os.environ.get("NOWCERTS_USERNAME", "")
        self._password = password or os.environ.get("NOWCERTS_PASSWORD", "")
        self.timeout = timeout
        self._token: str | None = None
        if not self._username or not self._password:
            raise NowCertsClientError(
                "NOWCERTS_USERNAME and NOWCERTS_PASSWORD must be set (env or constructor)."
            )

    def _authenticate(self) -> str:
        """Fetch a fresh bearer token via grant_type=password."""
        url = f"{self.base_url}{self.TOKEN_ENDPOINT}"
        resp = requests.post(
            url,
            data={
                "grant_type": "password",
                "username": self._username,
                "password": self._password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        if not resp.ok:
            raise NowCertsClientError(
                f"NowCerts auth failed {resp.status_code}: {resp.text[:500]}"
            )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise NowCertsClientError("NowCerts auth response missing access_token")
        self._token = token
        log.info("NowCerts: authenticated successfully")
        return token

    def _headers(self) -> dict[str, str]:
        if not self._token:
            self._authenticate()
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """GET with auto-retry on 401 (token expired)."""
        url = f"{self.base_url}{path}"
        for attempt in range(2):
            resp = requests.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            if resp.status_code == 401 and attempt == 0:
                log.info("NowCerts: token expired, re-authenticating")
                self._authenticate()
                continue
            if not resp.ok:
                raise NowCertsClientError(
                    f"NowCerts GET {path} failed {resp.status_code}: {resp.text[:500]}"
                )
            return resp.json()
        raise NowCertsClientError(f"NowCerts GET {path}: auth retry exhausted")

    def fetch_insureds(
        self,
        *,
        page_size: int = 50,
        since: str | None = None,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch all Insured records with OData pagination.

        Args:
            page_size: records per page ($top).
            since: ISO datetime filter — only records with changeDate >= since.
            max_pages: safety cap on number of pages fetched.

        Returns:
            List of raw insured dicts from the NowCerts API.
        """
        all_records: list[dict[str, Any]] = []
        skip = 0
        for page in range(max_pages):
            params: dict[str, str] = {
                "$count": "true",
                "$orderby": "changeDate desc",
                "$skip": str(skip),
                "$top": str(page_size),
            }
            if since:
                params["$filter"] = f"changeDate ge datetime'{since}'"

            body = self._get("/api/InsuredDetailList", params=params)

            records = body if isinstance(body, list) else body.get("value", body.get("items", []))
            if not records:
                break

            all_records.extend(records)
            log.info(
                "NowCerts: fetched page %d (%d records, %d total so far)",
                page + 1,
                len(records),
                len(all_records),
            )

            if len(records) < page_size:
                break
            skip += page_size

        log.info("NowCerts: total insureds fetched = %d", len(all_records))
        return all_records

    def fetch_policies(
        self,
        *,
        page_size: int = 50,
        since: str | None = None,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch all Policy records with OData pagination."""
        all_records: list[dict[str, Any]] = []
        skip = 0
        for page in range(max_pages):
            params: dict[str, str] = {
                "$count": "true",
                "$orderby": "changeDate desc",
                "$skip": str(skip),
                "$top": str(page_size),
            }
            if since:
                params["$filter"] = f"changeDate ge datetime'{since}'"

            body = self._get("/api/PolicyDetailList", params=params)
            records = body if isinstance(body, list) else body.get("value", body.get("items", []))
            if not records:
                break

            all_records.extend(records)
            log.info(
                "NowCerts: fetched policy page %d (%d records, %d total)",
                page + 1,
                len(records),
                len(all_records),
            )

            if len(records) < page_size:
                break
            skip += page_size

        log.info("NowCerts: total policies fetched = %d", len(all_records))
        return all_records
