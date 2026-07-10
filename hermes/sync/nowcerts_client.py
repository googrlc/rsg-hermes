"""NowCerts API client: bearer-token auth, OData pagination."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

log = logging.getLogger(__name__)

_TZ_OFFSET_RE = re.compile(r"[+\-]\d{2}:\d{2}$")


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
        page_size: int = 100,
        since: str | None = None,
        max_pages: int = 1000,
    ) -> list[dict[str, Any]]:
        """Fetch all Insured records with OData pagination.

        Args:
            page_size: records per page ($top).
            since: ISO datetime filter — only records with changeDate >= since.
            max_pages: safety cap on number of pages fetched. Pagination stops
                early on the first partial/empty page, so this only bounds a
                runaway full backfill. Defaults are sized for a full first load
                (page_size * max_pages = 100k); a `since` incremental stops
                after a page or two.

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
                ts = since if (since.endswith("Z") or _TZ_OFFSET_RE.search(since)) else f"{since}Z"
                params["$filter"] = f"changeDate ge {ts}"

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
        else:
            log.warning(
                "NowCerts: hit max_pages=%d (page_size=%d) — insured list may be "
                "TRUNCATED at %d records; raise max_pages for a complete backfill.",
                max_pages, page_size, len(all_records),
            )

        log.info("NowCerts: total insureds fetched = %d", len(all_records))
        return all_records

    def fetch_policies(
        self,
        *,
        page_size: int = 100,
        since: str | None = None,
        max_pages: int = 1000,
    ) -> list[dict[str, Any]]:
        """Fetch all Policy records with OData pagination.

        Defaults are sized for a full first load (page_size * max_pages =
        100k); pagination stops early on the first partial page, so a `since`
        incremental stops after a page or two.
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
                ts = since if (since.endswith("Z") or _TZ_OFFSET_RE.search(since)) else f"{since}Z"
                params["$filter"] = f"changeDate ge {ts}"

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
        else:
            log.warning(
                "NowCerts: hit max_pages=%d (page_size=%d) — policy list may be "
                "TRUNCATED at %d records; raise max_pages for a complete backfill.",
                max_pages, page_size, len(all_records),
            )

        log.info("NowCerts: total policies fetched = %d", len(all_records))
        return all_records

    # ── Write methods ─────────────────────────────────────────────────────

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        """POST with auto-retry on 401 (token expired)."""
        url = f"{self.base_url}{path}"
        for attempt in range(2):
            headers = {**self._headers(), "Content-Type": "application/json"}
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code == 401 and attempt == 0:
                log.info("NowCerts: token expired, re-authenticating")
                self._authenticate()
                continue
            if not resp.ok:
                raise NowCertsClientError(
                    f"NowCerts POST {path} failed {resp.status_code}: {resp.text[:500]}"
                )
            if resp.content:
                return resp.json()
            return {}
        raise NowCertsClientError(f"NowCerts POST {path}: auth retry exhausted")

    def _patch(self, path: str, payload: dict[str, Any]) -> Any:
        """PATCH with auto-retry on 401 (token expired)."""
        url = f"{self.base_url}{path}"
        for attempt in range(2):
            headers = {**self._headers(), "Content-Type": "application/json"}
            resp = requests.patch(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code == 401 and attempt == 0:
                log.info("NowCerts: token expired, re-authenticating")
                self._authenticate()
                continue
            if not resp.ok:
                raise NowCertsClientError(
                    f"NowCerts PATCH {path} failed {resp.status_code}: {resp.text[:500]}"
                )
            if resp.content:
                return resp.json()
            return {}
        raise NowCertsClientError(f"NowCerts PATCH {path}: auth retry exhausted")

    def create_insured(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create or update an insured/prospect in NowCerts.

        POST /api/Insured/Insert — upserts on DatabaseId, CommercialName,
        or FirstName+LastName.

        Args:
            payload: NowCerts Insured fields (CommercialName, FirstName,
                     LastName, FEIN, AddressLine1, etc.)

        Returns:
            API response dict.
        """
        log.info("NowCerts: creating/updating insured: %s", payload.get("CommercialName", "?"))
        return self._post("/api/Insured/Insert", payload)

    def create_insured_with_policies(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create or update an insured with policies in a single call.

        POST /api/InsuredAndPolicies/Insert

        Args:
            payload: InsuredAndPolicies body with top-level insured fields
                     plus optional 'Policies' and 'Quotes' arrays.

        Returns:
            API response dict.
        """
        log.info(
            "NowCerts: creating insured+policies: %s (%d policies)",
            payload.get("CommercialName", "?"),
            len(payload.get("Policies", [])),
        )
        return self._post("/api/InsuredAndPolicies/Insert", payload)

    def insert_policy(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create or update a policy in NowCerts.

        POST /api/Policy/Insert

        Args:
            payload: NcPolicyOrQuoteMatch fields (Number, EffectiveDate,
                     Premium, AgencyCommissionPercent, etc.)

        Returns:
            API response dict.
        """
        log.info(
            "NowCerts: inserting policy: %s for insured %s",
            payload.get("Number", "?"),
            payload.get("InsuredName", payload.get("InsuredDatabaseId", "?")),
        )
        return self._post("/api/Policy/Insert", payload)

    def update_policy(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Partially update a policy in NowCerts.

        PATCH /api/Policy/PartialUpdate — requires DatabaseId.

        Args:
            payload: Fields to update (DatabaseId required).

        Returns:
            API response dict.
        """
        log.info("NowCerts: updating policy: %s", payload.get("DatabaseId", "?"))
        return self._patch("/api/Policy/PartialUpdate", payload)

    def insert_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create or update a NowCerts Task — the AMS activity-ledger entry.

        POST /api/Zapier/InsertTask ("Insert/update task"). Task writes live
        under the Zapier namespace; there is no /api/Task/* controller. The
        request body is **snake_case** (unlike the camelCase TasksList read):
        required ``title``, ``status``, ``priority``, ``due_date``; link a client
        via ``insured_database_id`` (the insured GUID) and optionally a policy
        via ``policy_number`` / ``policy_database_id``; ``category_name`` /
        ``stage_name`` carry the service-request type; pass ``database_id`` to
        update an existing task instead of creating one.

        Returns:
            API response dict (carries the task's database_id for idempotency).
        """
        log.info(
            "NowCerts: upserting task %r for insured %s",
            payload.get("title", "?"),
            payload.get("insured_database_id", "?"),
        )
        return self._post("/api/Zapier/InsertTask", payload)

    def update_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Update an existing NowCerts Task (e.g. flip status to Closed).

        POST /api/Zapier/UpdateTask — requires ``database_id``. InsertTask also
        upserts by ``database_id``, but this is the explicit update route.
        """
        log.info("NowCerts: updating task %s", payload.get("database_id", "?"))
        return self._post("/api/Zapier/UpdateTask", payload)

    def insert_insured_no_override(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create/enrich an insured WITHOUT clobbering existing AMS fields.

        POST /api/Insured/InsertNoOverride — preserves fields that are not sent
        (verified: sending ``{DatabaseId, Active}`` leaves name/email/phone
        intact). Use this for the new-client stub (on Opportunity Closed Won)
        and for fill-blank corrections so Espo can never overwrite the AMS
        source of truth. Dedupe against the AMS first to avoid duplicate
        insureds. Body is PascalCase like ``create_insured``.

        Returns:
            API response dict (carries the insured's DatabaseId GUID).
        """
        log.info(
            "NowCerts: no-override upsert insured: %s",
            payload.get("CommercialName", payload.get("DatabaseId", "?")),
        )
        return self._post("/api/Insured/InsertNoOverride", payload)
