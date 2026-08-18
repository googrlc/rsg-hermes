"""Zoho CRM REST API client: OAuth2 refresh-token auth, retry, singleton.

Mirrors the NowCertsClient shape (process-wide ``get_client()``, bearer auth
with re-auth on 401, exponential backoff) so intake / CRM writers share one
pattern. Writes Accounts, Contacts, Deals, Notes, Attachments, and Tags —
nothing here stages AMS / Momentum work.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 4
_RETRY_BACKOFF_SECONDS = 2.0
# Refresh a few minutes early so a long request never races the expiry wall.
_TOKEN_EXPIRY_SKEW_SECONDS = 120

# New Business pipeline on this Zoho org (Deals).
DEFAULT_PIPELINE_ID = "7529682000000697038"

_shared: "ZohoClient | None" = None
_shared_lock = threading.Lock()


def _env_float(name: str, fallback: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return fallback


def get_client() -> "ZohoClient":
    """Process-wide Zoho client — prefer this over ``ZohoClient()``."""
    global _shared
    if _shared is None:
        with _shared_lock:
            if _shared is None:
                _shared = ZohoClient()
    return _shared


def reset_client() -> None:
    """Drop the singleton (tests / credential rotation)."""
    global _shared
    with _shared_lock:
        _shared = None


class ZohoClientError(Exception):
    """Raised on auth failures or non-success Zoho API responses."""


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _escape_criteria_value(value: str) -> str:
    """Escape Zoho search criteria special characters in a field value."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _pick(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and _present(data[key]):
            return data[key]
    return None


class ZohoClient:
    """Thin REST client for Zoho CRM v2.

    Auth: POST accounts.zoho.{dc}/oauth/v2/token with grant_type=refresh_token.
    API:  https://www.zohoapis.{dc}/crm/v2/
    """

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        data_center: str | None = None,
        timeout: float | None = None,
        retries: int | None = None,
        pipeline_id: str | None = None,
    ) -> None:
        self.client_id = client_id or os.environ.get("ZOHO_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("ZOHO_CLIENT_SECRET", "")
        self.refresh_token = refresh_token or os.environ.get("ZOHO_REFRESH_TOKEN", "")
        dc = (data_center or os.environ.get("ZOHO_DATA_CENTER", "com") or "com").strip().lstrip(".")
        self.data_center = dc or "com"
        self.accounts_base = f"https://accounts.zoho.{self.data_center}"
        self.api_base = f"https://www.zohoapis.{self.data_center}/crm/v2"
        self.timeout = (
            timeout if timeout is not None else _env_float("ZOHO_TIMEOUT", DEFAULT_TIMEOUT)
        )
        self.retries = (
            retries if retries is not None else int(_env_float("ZOHO_RETRIES", DEFAULT_RETRIES))
        )
        self.pipeline_id = (
            pipeline_id
            or os.environ.get("ZOHO_PIPELINE_ID", DEFAULT_PIPELINE_ID)
            or DEFAULT_PIPELINE_ID
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._auth_lock = threading.Lock()
        self._user_id_by_email: dict[str, str] = {}
        if not self.client_id or not self.client_secret or not self.refresh_token:
            raise ZohoClientError(
                "ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, and ZOHO_REFRESH_TOKEN must be set "
                "(env or constructor)."
            )

    # ── Auth ──────────────────────────────────────────────────────────────

    def _authenticate(self) -> str:
        """Exchange the refresh token for a fresh access token."""
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
            raise ZohoClientError(
                f"Zoho auth failed {resp.status_code}: {resp.text[:500]}"
            )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise ZohoClientError(
                f"Zoho auth response missing access_token: {str(body)[:300]}"
            )
        expires_in = int(body.get("expires_in") or 3600)
        self._token = token
        self._token_expires_at = time.time() + max(60, expires_in - _TOKEN_EXPIRY_SKEW_SECONDS)
        log.info("Zoho: authenticated successfully (dc=%s)", self.data_center)
        return token

    def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        with self._auth_lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token
            return self._authenticate()

    def _headers(self, *, json_body: bool = True) -> dict[str, str]:
        headers = {
            "Authorization": f"Zoho-oauthtoken {self._ensure_token()}",
            "Accept": "application/json",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    # ── HTTP primitives ───────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        files: dict[str, Any] | None = None,
        data: Any = None,
    ) -> Any:
        """HTTP with retry on 401 (re-auth) and 429 (rate limit / backoff)."""
        url = path if path.startswith("http") else f"{self.api_base}/{path.lstrip('/')}"
        last: Exception | None = None
        attempts = max(1, self.retries + 1)

        for attempt in range(attempts):
            try:
                headers = self._headers(json_body=files is None and data is None)
                if files is not None:
                    # Let requests set multipart Content-Type with boundary.
                    headers.pop("Content-Type", None)
                resp = requests.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    files=files,
                    data=data,
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last = exc
                if attempt + 1 < attempts:
                    delay = _RETRY_BACKOFF_SECONDS * (2 ** attempt)
                    log.info(
                        "Zoho: %s on %s %s (attempt %d/%d); retrying in %.0fs",
                        type(exc).__name__,
                        method,
                        path,
                        attempt + 1,
                        attempts,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise ZohoClientError(
                    f"Zoho {method} {path}: {attempts} attempts all timed out"
                ) from last

            if resp.status_code == 401:
                log.info("Zoho: token expired/invalid, re-authenticating")
                with self._auth_lock:
                    self._authenticate()
                if attempt + 1 < attempts:
                    continue
                raise ZohoClientError(
                    f"Zoho {method} {path} failed 401 after re-auth: {resp.text[:500]}"
                )

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else _RETRY_BACKOFF_SECONDS * (2 ** attempt)
                except ValueError:
                    delay = _RETRY_BACKOFF_SECONDS * (2 ** attempt)
                log.warning(
                    "Zoho: rate limited on %s %s (attempt %d/%d); sleeping %.1fs",
                    method,
                    path,
                    attempt + 1,
                    attempts,
                    delay,
                )
                if attempt + 1 < attempts:
                    time.sleep(delay)
                    continue
                raise ZohoClientError(
                    f"Zoho {method} {path} rate limited after {attempts} attempts"
                )

            # Search: no matches → 204
            if resp.status_code == 204:
                return {"data": []}

            if not resp.ok:
                raise ZohoClientError(
                    f"Zoho {method} {path} failed {resp.status_code}: {resp.text[:500]}"
                )
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError as exc:
                raise ZohoClientError(
                    f"Zoho {method} {path}: non-JSON response: {resp.text[:300]}"
                ) from exc

        raise ZohoClientError(f"Zoho {method} {path}: retry budget exhausted") from last

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, payload: Any = None, **kwargs: Any) -> Any:
        return self._request("POST", path, json_body=payload, **kwargs)

    def _put(self, path: str, payload: Any) -> Any:
        return self._request("PUT", path, json_body=payload)

    @staticmethod
    def _record_id(body: Any) -> str | None:
        if not isinstance(body, dict):
            return None
        data = body.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                details = first.get("details") or {}
                if isinstance(details, dict) and details.get("id"):
                    return str(details["id"])
                if first.get("id"):
                    return str(first["id"])
                if first.get("code") and first.get("code") != "SUCCESS":
                    return None
        if body.get("id"):
            return str(body["id"])
        return None

    @staticmethod
    def _assert_write_ok(body: Any, *, action: str, module: str) -> str:
        rid = ZohoClient._record_id(body)
        if rid:
            return rid
        # Surface Zoho's per-row error when present.
        detail = ""
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, list) and data and isinstance(data[0], dict):
                row = data[0]
                detail = f"{row.get('code')}: {row.get('message')}"
                if row.get("details"):
                    detail = f"{detail} {row.get('details')}"
        raise ZohoClientError(
            f"Zoho {action} {module} returned no record id: {detail or str(body)[:300]}"
        )

    # ── Search / users ────────────────────────────────────────────────────

    def search_records(self, module: str, criteria: str) -> list[dict[str, Any]]:
        """GET /{module}/search?criteria=… — returns matching records (possibly empty)."""
        if not criteria:
            return []
        return self.list_records(module, criteria=criteria)

    def list_records(
        self,
        module: str,
        *,
        criteria: str | None = None,
        page: int = 1,
        per_page: int = 200,
        fields: str | None = None,
    ) -> list[dict[str, Any]]:
        """One page of CRM records. Search when ``criteria`` is set, else list."""
        params: dict[str, Any] = {"page": page, "per_page": min(200, max(1, per_page))}
        if fields:
            params["fields"] = fields
        path = f"{module}/search" if criteria else module
        if criteria:
            params["criteria"] = criteria
        try:
            body = self._get(path, params=params)
        except ZohoClientError as exc:
            msg = str(exc).lower()
            if "204" in msg or "no records" in msg or " no data" in msg:
                return []
            raise
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return []
        return [row for row in data if isinstance(row, dict)]

    def iter_records(
        self,
        module: str,
        *,
        criteria: str | None = None,
        per_page: int = 200,
        max_pages: int = 50,
        fields: str | None = None,
    ):
        """Yield CRM records across pages. Stops on a short page or empty page."""
        for page in range(1, max_pages + 1):
            rows = self.list_records(
                module, criteria=criteria, page=page, per_page=per_page, fields=fields
            )
            if not rows:
                return
            yield from rows
            if len(rows) < per_page:
                return

    def get_record(self, module: str, record_id: str) -> dict[str, Any] | None:
        """GET /{module}/{id}. Missing record → None."""
        try:
            body = self._get(f"{module}/{record_id}")
        except ZohoClientError as exc:
            msg = str(exc).lower()
            if "204" in msg or "404" in msg or "no records" in msg:
                return None
            raise
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if isinstance(data, dict):
            return data
        return None

    def _find_first(self, module: str, criteria: str) -> dict[str, Any] | None:
        rows = self.search_records(module, criteria)
        return rows[0] if rows else None

    def _resolve_owner_id(self, email: str) -> str | None:
        """Map a user email → Zoho user id (cached)."""
        key = (email or "").strip().lower()
        if not key:
            return None
        if key in self._user_id_by_email:
            return self._user_id_by_email[key]
        body = self._get("users", params={"type": "ActiveUsers"})
        users = body.get("users") if isinstance(body, dict) else None
        if not isinstance(users, list):
            return None
        for user in users:
            if not isinstance(user, dict):
                continue
            uemail = str(user.get("email") or "").strip().lower()
            uid = user.get("id")
            if uemail and uid:
                self._user_id_by_email[uemail] = str(uid)
        return self._user_id_by_email.get(key)

    # ── Field maps ────────────────────────────────────────────────────────

    def _map_account(self, account_data: dict[str, Any]) -> dict[str, Any]:
        mapping = {
            "Account_Name": _pick(account_data, "account_name", "Account_Name", "name", "commercial_name"),
            "Legal_Name": _pick(account_data, "legal_name", "Legal_Name"),
            "DBA": _pick(account_data, "dba", "DBA"),
            "FEIN": _pick(account_data, "fein", "FEIN", "ein"),
            "Entity_Type": _pick(account_data, "entity_type", "Entity_Type"),
            "Insured_Type": _pick(account_data, "insured_type", "Insured_Type", "segment"),
            "Operations_Summary": _pick(account_data, "operations_summary", "Operations_Summary"),
            "Estimated_Payroll": _pick(account_data, "estimated_payroll", "Estimated_Payroll"),
            "NAICS_Code": _pick(account_data, "naics", "NAICS_Code", "naics_code"),
            "Email": _pick(account_data, "email", "Email"),
            "Phone": _pick(account_data, "phone", "Phone"),
            "Mobile": _pick(account_data, "cell_phone", "mobile", "Mobile"),
            "Billing_Street": _pick(account_data, "address", "Billing_Street", "street"),
            "Billing_City": _pick(account_data, "city", "Billing_City"),
            "Billing_State": _pick(account_data, "state", "Billing_State"),
            "Billing_Code": _pick(account_data, "zip", "Billing_Code", "postal_code"),
            "Nextcloud_Folder_URL": _pick(
                account_data, "nextcloud_folder_url", "Nextcloud_Folder_URL", "nextcloud_folder"
            ),
            "NowCerts_Insured_GUID": _pick(
                account_data,
                "nowcerts_insured_guid",
                "NowCerts_Insured_GUID",
                "insured_database_id",
                "insured_id",
            ),
        }
        return {k: v for k, v in mapping.items() if _present(v)}

    def _map_contact(self, contact_data: dict[str, Any], account_id: str) -> dict[str, Any]:
        first = _pick(contact_data, "first_name", "First_Name")
        last = _pick(contact_data, "last_name", "Last_Name")
        full = _pick(contact_data, "full_name", "Full_Name")
        if not first and not last and full:
            parts = str(full).split(None, 1)
            first = parts[0] if parts else None
            last = parts[1] if len(parts) > 1 else None
        # Zoho requires Last_Name on Contacts.
        if not last:
            last = first or full or "Unknown"
            if first == last:
                first = None

        primary = contact_data.get("primary_contact", contact_data.get("Is_Primary_Contact"))
        if isinstance(primary, str):
            primary = primary.strip().lower() in ("1", "true", "yes", "y")

        mapping = {
            "First_Name": first,
            "Last_Name": last,
            "Email": _pick(contact_data, "email", "Email"),
            "Phone": _pick(contact_data, "phone", "Phone"),
            "Mobile": _pick(contact_data, "mobile", "Mobile", "cell_phone"),
            "ContactRole": _pick(contact_data, "role", "ContactRole", "contact_role"),
            "Household_Role": _pick(contact_data, "household_role", "Household_Role"),
            "Relationship_To_Account": _pick(
                contact_data, "relationship_to_account", "Relationship_To_Account"
            ),
            "Is_Primary_Contact": primary if primary is not None else None,
            "Account_Name": {"id": str(account_id)} if account_id else None,
        }
        return {k: v for k, v in mapping.items() if _present(v) or v is False}

    def _map_deal(self, deal_data: dict[str, Any], account_id: str) -> dict[str, Any]:
        owner_email = _pick(deal_data, "producer_email", "Owner", "owner_email", "assigned_to_email")
        owner_id = None
        if isinstance(owner_email, dict):
            owner_id = owner_email.get("id")
        elif owner_email and "@" in str(owner_email):
            owner_id = self._resolve_owner_id(str(owner_email))
        elif owner_email and str(owner_email).isdigit():
            owner_id = str(owner_email)

        mapping = {
            "Deal_Name": _pick(
                deal_data, "opportunity_name", "Deal_Name", "deal_name", "name"
            ),
            "Line_of_Business": _pick(
                deal_data, "line_of_business", "Line_of_Business", "lob"
            ),
            "Stage": _pick(deal_data, "stage", "Stage") or "Not Assigned",
            "Opportunity_Type": _pick(
                deal_data, "opportunity_type", "Opportunity_Type"
            )
            or "New Business",
            "Carrier": _pick(deal_data, "carrier", "Carrier"),
            "Premium_Estimate": _pick(
                deal_data, "premium", "premium_estimate", "Premium_Estimate", "total"
            ),
            "Effective_Date": _pick(
                deal_data, "proposed_effective_date", "Effective_Date", "effective_date"
            ),
            "Expiration_Date": _pick(deal_data, "x_date", "Expiration_Date", "expiration_date"),
            "Intake_Source": _pick(deal_data, "source", "Intake_Source", "lead_source"),
            "Description": _pick(deal_data, "description", "Description"),
            "Account_Name": {"id": str(account_id)} if account_id else None,
            "NowCerts_Opportunity_ID": _pick(
                deal_data,
                "nowcerts_opportunity_id",
                "NowCerts_Opportunity_ID",
                "opportunity_id",
            ),
            "Stage_Option_ID": _pick(deal_data, "stage_option_id", "Stage_Option_ID"),
            "Client_Identifier": _pick(deal_data, "client_identifier", "Client_Identifier"),
            "Insured_Name": _pick(deal_data, "insured_name", "Insured_Name"),
            "Prospect_Type": _pick(deal_data, "prospect_type", "Prospect_Type") or "Prospect",
            "Insured_Type": _pick(deal_data, "insured_type", "Insured_Type"),
            "Win_Likelihood": _pick(deal_data, "win_likelihood", "Win_Likelihood") or "Good",
            "Pipeline_ID": self.pipeline_id,
        }
        if owner_id:
            mapping["Owner"] = {"id": str(owner_id)}
        # Deal_Name is required — synthesize from LOB if needed.
        if not mapping.get("Deal_Name") and mapping.get("Line_of_Business"):
            mapping["Deal_Name"] = str(mapping["Line_of_Business"])
        return {k: v for k, v in mapping.items() if _present(v)}

    # ── Public write methods ──────────────────────────────────────────────

    def create_or_update_account(self, account_data: dict[str, Any]) -> dict[str, Any]:
        """Upsert an Account by NowCerts GUID (preferred) then Account_Name."""
        payload = self._map_account(account_data)
        name = payload.get("Account_Name")
        guid = payload.get("NowCerts_Insured_GUID")
        if not name and not guid:
            raise ZohoClientError("create_or_update_account requires account_name or nowcerts_insured_guid")

        existing: dict[str, Any] | None = None
        if guid:
            existing = self._find_first(
                "Accounts",
                f"(NowCerts_Insured_GUID:equals:{_escape_criteria_value(str(guid))})",
            )
        if existing is None and name:
            existing = self._find_first(
                "Accounts",
                f"(Account_Name:equals:{_escape_criteria_value(str(name))})",
            )

        if existing and existing.get("id"):
            zoho_id = str(existing["id"])
            body = self._put("Accounts", {"data": [{"id": zoho_id, **payload}]})
            rid = self._assert_write_ok(body, action="update", module="Accounts")
            log.info("Zoho: updated Account id=%s name=%r", rid, name)
            return {"id": rid, "action": "updated"}

        body = self._post("Accounts", {"data": [payload]})
        rid = self._assert_write_ok(body, action="create", module="Accounts")
        log.info("Zoho: created Account id=%s name=%r", rid, name)
        return {"id": rid, "action": "created"}

    def create_or_update_contact(
        self, contact_data: dict[str, Any], account_id: str
    ) -> dict[str, Any]:
        """Upsert a Contact by email, then by name + account."""
        if not account_id:
            raise ZohoClientError("create_or_update_contact requires account_id")
        payload = self._map_contact(contact_data, account_id)
        email = payload.get("Email")
        first = payload.get("First_Name")
        last = payload.get("Last_Name")

        existing: dict[str, Any] | None = None
        if email:
            existing = self._find_first(
                "Contacts",
                f"(Email:equals:{_escape_criteria_value(str(email))})",
            )
        if existing is None and last:
            parts = [
                f"(Last_Name:equals:{_escape_criteria_value(str(last))})",
                f"(Account_Name.id:equals:{_escape_criteria_value(str(account_id))})",
            ]
            if first:
                parts.insert(
                    0, f"(First_Name:equals:{_escape_criteria_value(str(first))})"
                )
            criteria = parts[0] if len(parts) == 1 else f"({'and'.join(parts)})"
            existing = self._find_first("Contacts", criteria)

        if existing and existing.get("id"):
            zoho_id = str(existing["id"])
            body = self._put("Contacts", {"data": [{"id": zoho_id, **payload}]})
            rid = self._assert_write_ok(body, action="update", module="Contacts")
            log.info("Zoho: updated Contact id=%s email=%r", rid, email)
            return {"id": rid, "action": "updated"}

        body = self._post("Contacts", {"data": [payload]})
        rid = self._assert_write_ok(body, action="create", module="Contacts")
        log.info("Zoho: created Contact id=%s email=%r", rid, email)
        return {"id": rid, "action": "created"}

    def create_deal(self, deal_data: dict[str, Any], account_id: str) -> dict[str, Any]:
        """Always create a new Deal (one per LOB) linked to the Account."""
        if not account_id:
            raise ZohoClientError("create_deal requires account_id")
        payload = self._map_deal(deal_data, account_id)
        if not payload.get("Deal_Name"):
            raise ZohoClientError("create_deal requires opportunity_name / Deal_Name")

        body = self._post("Deals", {"data": [payload]})
        rid = self._assert_write_ok(body, action="create", module="Deals")
        log.info(
            "Zoho: created Deal id=%s name=%r lob=%r",
            rid,
            payload.get("Deal_Name"),
            payload.get("Line_of_Business"),
        )
        return {"id": rid}

    def upload_attachment(
        self, record_module: str, record_id: str, file_path: str
    ) -> dict[str, Any]:
        """POST /{module}/{id}/Attachments — multipart file upload."""
        path = Path(file_path)
        if not path.is_file():
            raise ZohoClientError(f"attachment file not found: {file_path}")
        with path.open("rb") as fh:
            body = self._request(
                "POST",
                f"{record_module}/{record_id}/Attachments",
                files={"file": (path.name, fh)},
            )
        rid = self._assert_write_ok(body, action="attach", module=record_module)
        log.info(
            "Zoho: uploaded attachment id=%s module=%s record=%s file=%s",
            rid,
            record_module,
            record_id,
            path.name,
        )
        return {"id": rid}

    def create_note(
        self,
        record_module: str,
        record_id: str,
        title: str,
        content: str,
    ) -> dict[str, Any]:
        """POST /Notes linked to a parent record."""
        payload = {
            "Note_Title": title or "Intake Note",
            "Note_Content": content or "",
            "Parent_Id": str(record_id),
            "se_module": record_module,
        }
        body = self._post("Notes", {"data": [payload]})
        rid = self._assert_write_ok(body, action="create", module="Notes")
        log.info(
            "Zoho: created Note id=%s parent=%s/%s title=%r",
            rid,
            record_module,
            record_id,
            title,
        )
        return {"id": rid}

    def add_tag(self, record_module: str, record_id: str, tag: str) -> dict[str, Any]:
        """POST /{module}/{id}/actions/add_tags."""
        if not tag:
            raise ZohoClientError("add_tag requires a tag name")
        body = self._post(
            f"{record_module}/{record_id}/actions/add_tags",
            {"tags": [{"name": str(tag)}]},
        )
        log.info("Zoho: added tag %r on %s/%s", tag, record_module, record_id)
        return body if isinstance(body, dict) else {"result": body}

    def upsert_by_field(
        self,
        module: str,
        record: dict[str, Any],
        *,
        match_field: str,
        match_value: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a record matched on ``match_field`` (e.g. an external GUID).

        Used by the Momentum→Zoho backfill for Policies / Renewals custom modules.
        Returns ``{"id": zoho_id, "action": "created"|"updated"}``.
        """
        value = match_value if match_value is not None else record.get(match_field)
        if not value:
            raise ZohoClientError(
                f"upsert_by_field({module}): match_field {match_field!r} has no value"
            )
        existing = self._find_first(
            module,
            f"({match_field}:equals:{_escape_criteria_value(str(value))})",
        )
        payload = {k: v for k, v in record.items() if _present(v) or v is False}
        if match_field not in payload:
            payload[match_field] = value

        if existing and existing.get("id"):
            zoho_id = str(existing["id"])
            body = self._put(module, {"data": [{"id": zoho_id, **payload}]})
            rid = self._assert_write_ok(body, action="update", module=module)
            log.info("Zoho: updated %s id=%s %s=%r", module, rid, match_field, value)
            return {"id": rid, "action": "updated"}

        body = self._post(module, {"data": [payload]})
        rid = self._assert_write_ok(body, action="create", module=module)
        log.info("Zoho: created %s id=%s %s=%r", module, rid, match_field, value)
        return {"id": rid, "action": "created"}
