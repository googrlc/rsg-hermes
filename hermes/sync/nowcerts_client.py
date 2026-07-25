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

    def fetch_opportunities(
        self,
        *,
        page_size: int = 100,
        max_pages: int = 1000,
    ) -> list[dict[str, Any]]:
        """Fetch all Opportunity records from the OData ``OpportunitiesList`` endpoint.

        A NowCerts Opportunity is a first-class object (distinct from a policy/quote):
        the agency's pipeline. ``OpportunitiesList`` requires ``$top``, ``$skip`` and
        ``$orderby`` together (a 400 otherwise). Returns raw dicts shaped like
        ``OpportunityIntegrationModel``: ``id``, ``insuredDatabaseId``,
        ``lineOfBusinessName``, ``opportunityStageName``, ``neededBy``,
        ``currentStageDueDate``, ``referralSourceName``, ``winProbability`` (a
        categorical Excellent..NotLikely), ``description``, ``assignedTo``,
        ``createdFromRenewal``.
        """
        all_records: list[dict[str, Any]] = []
        skip = 0
        for page in range(max_pages):
            body = self._get(
                "/api/OpportunitiesList",
                params={"$top": str(page_size), "$skip": str(skip), "$orderby": "neededBy asc"},
            )
            records = body if isinstance(body, list) else body.get("value", body.get("items", []))
            if not records:
                break
            all_records.extend(records)
            if len(records) < page_size:
                break
            skip += page_size
        log.info("NowCerts: total opportunities fetched = %d", len(all_records))
        return all_records

    def find_opportunity(self, opportunity_id: str) -> dict[str, Any] | None:
        """Return one Opportunity's current fields by its NowCerts id — the round-trip
        source for the writeback (so required fields aren't blanked). Scans
        ``OpportunitiesList`` (the opportunity book is small)."""
        if not opportunity_id:
            return None
        for o in self.fetch_opportunities(page_size=100):
            if str(o.get("id") or o.get("databaseId") or "") == str(opportunity_id):
                return o
        return None

    # ── Client-360 read helpers (live single-client lookups) ──────────────
    # These power the CRM Desk assistant's live AMS reads. Unlike fetch_* (bulk
    # backfill of the whole book), each targets one insured so a chat question
    # doesn't pull 100k rows. All read-only.

    def search_insureds(self, name: str, *, top: int = 10) -> list[dict[str, Any]]:
        """Live fuzzy search of the AMS insured book by commercial name.

        GET /api/InsuredDetailList with an OData ``contains()`` filter — returns
        the rich insured shape (``databaseId``/``id``, ``commercialName``,
        ``active``, contact + address fields). Read-only. Returns ``[]`` on no
        match or an unfilterable/error response.
        """
        q = (name or "").strip()
        if not q:
            return []
        esc = q.replace("'", "''")  # OData single-quote escape
        try:
            body = self._get(
                "/api/InsuredDetailList",
                params={
                    "$filter": f"contains(commercialName,'{esc}')",
                    "$orderby": "changeDate desc",
                    "$skip": "0",
                    "$top": str(max(1, min(int(top), 50))),
                },
            )
        except NowCertsClientError:
            return []
        rows = body if isinstance(body, list) else body.get("value", body.get("items", []))
        return [r for r in rows if isinstance(r, dict)]

    def policies_for_insured(self, insured_database_id: str, *, top: int = 100) -> list[dict[str, Any]]:
        """Live policies for one insured GUID.

        GET /api/PolicyDetailList filtered on ``insuredDatabaseId`` — the same
        rich shape ``fetch_policies`` returns. Read-only. Returns ``[]`` on no
        match or an error.
        """
        guid = (insured_database_id or "").strip()
        if not guid:
            return []
        esc = guid.replace("'", "''")  # OData single-quote escape
        try:
            body = self._get(
                "/api/PolicyDetailList",
                params={
                    "$filter": f"insuredDatabaseId eq '{esc}'",
                    "$orderby": "changeDate desc",
                    "$skip": "0",
                    "$top": str(max(1, min(int(top), 200))),
                },
            )
        except NowCertsClientError:
            return []
        rows = body if isinstance(body, list) else body.get("value", body.get("items", []))
        return [r for r in rows if isinstance(r, dict)]

    def opportunities_for_insured(self, insured_database_id: str) -> list[dict[str, Any]]:
        """Live opportunities for one insured GUID.

        Client-side filter of the (small) opportunity book — see
        ``fetch_opportunities``. Read-only.
        """
        guid = (insured_database_id or "").strip()
        if not guid:
            return []
        out: list[dict[str, Any]] = []
        for o in self.fetch_opportunities(page_size=100):
            oid = str(o.get("insuredDatabaseId") or o.get("InsuredDatabaseId") or "")
            if oid and oid == guid:
                out.append(o)
        return out

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

    def insert_opportunity(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Insert/update a NowCerts Opportunity — POST /api/Zapier/InsertOpportunity.

        Upserts by ``databaseId`` (pass it to UPDATE an existing opportunity's stage
        etc.). Body is ``OpportunityIntegrationModel``; required fields
        (lineOfBusinessName, neededBy, opportunityStageName, winProbability,
        agencyCommission, assignedTo) must be present, so the writeback round-trips
        them from a fresh read and changes only the stage.
        """
        log.info(
            "NowCerts: upsert opportunity %s → stage=%s",
            payload.get("databaseId", "?"), payload.get("opportunityStageName"),
        )
        return self._post("/api/Zapier/InsertOpportunity", payload)

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

    def find_insured_id(
        self,
        *,
        email: str | None = None,
        commercial_name: str | None = None,
        fein: str | None = None,
    ) -> str | None:
        """Return an existing insured's DatabaseId GUID by email, name, or FEIN.

        Dedup helper for the new-client stub — checks the AMS before creating an
        insured so we don't spawn duplicates. Tries the most reliable keys first
        (email, then FEIN, then commercial name). GET /api/InsuredList supports
        ``$filter`` on these fields (the ``id`` GUID field is not filterable).
        Returns None if nothing matches.
        """
        def _q(val: str) -> str:
            return val.replace("'", "''")  # OData single-quote escape

        filters: list[str] = []
        if email:
            filters.append(f"eMail eq '{_q(email)}'")
        if fein:
            filters.append(f"fein eq '{_q(fein)}'")
        if commercial_name:
            filters.append(f"commercialName eq '{_q(commercial_name)}'")
        for flt in filters:
            try:
                body = self._get("/api/InsuredList", params={"$filter": flt})
            except NowCertsClientError:
                continue
            rows = body if isinstance(body, list) else body.get("value", [])
            if rows and isinstance(rows[0], dict) and rows[0].get("id"):
                return str(rows[0]["id"])
        return None

    def find_insured(
        self,
        *,
        email: str | None = None,
        commercial_name: str | None = None,
        fein: str | None = None,
    ) -> dict[str, Any] | None:
        """Return an existing insured's full row (not just the GUID) by email, FEIN, or name.

        Superset of ``find_insured_id`` — the returned InsuredList row carries the
        ``id`` GUID plus segment fields (``prospectType``, ``insuredType``,
        ``leadSources``) that opportunity priming pulls onto the pipeline row.
        Tries the most reliable keys first (email, then FEIN, then commercial name).
        Returns None if nothing matches.
        """
        def _q(val: str) -> str:
            return val.replace("'", "''")  # OData single-quote escape

        filters: list[str] = []
        if email:
            filters.append(f"eMail eq '{_q(email)}'")
        if fein:
            filters.append(f"fein eq '{_q(fein)}'")
        if commercial_name:
            filters.append(f"commercialName eq '{_q(commercial_name)}'")
        for flt in filters:
            try:
                body = self._get("/api/InsuredList", params={"$filter": flt})
            except NowCertsClientError:
                continue
            rows = body if isinstance(body, list) else body.get("value", [])
            if rows and isinstance(rows[0], dict) and rows[0].get("id"):
                return rows[0]
        return None

    def find_policy_by_number(self, number: str) -> dict[str, Any] | None:
        """Return a single policy's current detail record by policy number.

        GET /api/PolicyDetailList with ``$filter=number eq '<number>'`` — the same
        rich shape ``fetch_policies`` returns (carries ``databaseId`` plus current
        field values like premium/effective/expiration). The renewal executor uses
        this for the mandatory read-before-write and read-after-write verification
        on ``update_ams``. Returns None if no policy matches; the caller treats an
        ambiguous (>1) match as a block rather than guessing.
        """
        if not number:
            return None
        escaped = str(number).replace("'", "''")  # OData single-quote escape
        try:
            body = self._get("/api/PolicyDetailList", params={"$filter": f"number eq '{escaped}'"})
        except NowCertsClientError:
            return None
        rows = body if isinstance(body, list) else body.get("value", body.get("items", []))
        if not rows:
            return None
        if len(rows) > 1:
            # Duplicate policy numbers are a stop-and-escalate condition, not a
            # "pick the first" case — surface all matches so the caller can block.
            return {"_ambiguous": True, "matches": rows}
        return rows[0] if isinstance(rows[0], dict) else None

    def is_insured_active(self, insured_database_id: str) -> bool:
        """Best-effort live check of an insured's Active flag by GUID.

        Used by the renewal executor's execution-time revalidation. NowCerts does
        not reliably support ``$filter`` on the insured id GUID, so an empty or
        unfilterable response is treated as "unknown -> active" (the nightly
        candidate refresh already vets insured-active via the bulk InsuredDetailList,
        and the executor's hard safety is the policy lifecycle status). Returns
        False only when the AMS explicitly reports the insured inactive.
        """
        if not insured_database_id:
            return True
        q = str(insured_database_id).replace("'", "''")  # OData single-quote escape
        try:
            body = self._get("/api/InsuredList", params={"$filter": f"id eq '{q}'"})
        except NowCertsClientError:
            return True
        rows = body if isinstance(body, list) else body.get("value", [])
        if not rows or not isinstance(rows[0], dict):
            return True
        active = rows[0].get("active")
        return True if active is None else bool(active)

    def insert_insured_no_override(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create/enrich an insured WITHOUT clobbering existing AMS fields.

        POST /api/Insured/InsertNoOverride — preserves fields that are not sent
        (verified: sending ``{DatabaseId, Active}`` leaves name/email/phone
        intact). Use this for the new-client stub (on Opportunity Closed Won)
        and for fill-blank corrections so a caller can never overwrite the AMS
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
