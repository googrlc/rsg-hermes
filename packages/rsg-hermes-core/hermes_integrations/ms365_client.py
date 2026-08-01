"""Microsoft 365 (Graph API) client: app-only client-credentials auth.

Unattended server access to a mailbox via Microsoft Graph. Auth is the
OAuth2 client-credentials flow against an Entra app registration that has the
``Mail.ReadWrite`` *application* permission (admin-consented). No interactive
login, no per-user refresh tokens.

Scope is deliberately read + move/label only — there is **no** send method
here. The triage job reads the inbox, drops actionable mail into the Hermes
intake pipeline, and moves newsletters/noise into a quarantine folder.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_DEFAULT_SELECT = (
    "id,internetMessageId,subject,bodyPreview,receivedDateTime,"
    "isRead,categories,from,sender,toRecipients"
)


class MS365ClientError(Exception):
    """Raised on auth failures or non-success Graph responses."""


class MS365Client:
    """Thin Microsoft Graph client for one Entra app registration.

    Auth: POST https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
    with grant_type=client_credentials and scope
    https://graph.microsoft.com/.default — yields an app-only bearer token.

    All mailbox calls target /users/{mailbox}, so one app token can service
    every mailbox the app is permitted to access (optionally constrained by an
    Exchange Application Access Policy).
    """

    def __init__(
        self,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.tenant_id = tenant_id or os.environ.get("MS365_TENANT_ID", "")
        self.client_id = client_id or os.environ.get("MS365_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("MS365_CLIENT_SECRET", "")
        self.timeout = timeout
        self._token: str | None = None
        # Cache resolved folder ids per (mailbox, display_name) to avoid
        # re-querying the folder list on every message move.
        self._folder_cache: dict[tuple[str, str], str] = {}
        if not (self.tenant_id and self.client_id and self._client_secret):
            raise MS365ClientError(
                "MS365_TENANT_ID, MS365_CLIENT_ID and MS365_CLIENT_SECRET "
                "must be set (env or constructor)."
            )

    # ── Auth ──────────────────────────────────────────────────────────────

    def _authenticate(self) -> str:
        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        resp = requests.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        if not resp.ok:
            raise MS365ClientError(
                f"MS365 auth failed {resp.status_code}: {resp.text[:500]}"
            )
        token = resp.json().get("access_token")
        if not token:
            raise MS365ClientError("MS365 auth response missing access_token")
        self._token = token
        log.info("MS365: authenticated successfully (app-only)")
        return token

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        if not self._token:
            self._authenticate()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    # ── Low-level verbs (auto-retry once on 401) ──────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        for attempt in range(2):
            extra = {"Content-Type": "application/json"} if json_body is not None else None
            resp = requests.request(
                method,
                url,
                headers=self._headers(extra),
                params=params,
                json=json_body,
                timeout=self.timeout,
            )
            if resp.status_code == 401 and attempt == 0:
                log.info("MS365: token expired/invalid, re-authenticating")
                self._authenticate()
                continue
            if not resp.ok:
                raise MS365ClientError(
                    f"MS365 {method} {path} failed {resp.status_code}: {resp.text[:500]}"
                )
            if resp.content and resp.headers.get("Content-Type", "").startswith(
                "application/json"
            ):
                return resp.json()
            return {}
        raise MS365ClientError(f"MS365 {method} {path}: auth retry exhausted")

    @staticmethod
    def _mbx(mailbox: str) -> str:
        """URL-encode a mailbox UPN for use in a /users/{mailbox} path."""
        return quote(mailbox, safe="@.")

    # ── Reads ─────────────────────────────────────────────────────────────

    def list_inbox_messages(
        self,
        mailbox: str,
        *,
        since_iso: str | None = None,
        top: int = 50,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """Return Inbox messages, newest first, optionally since an ISO time.

        ``since_iso`` filters on ``receivedDateTime`` (e.g. ``2026-05-30T00:00:00Z``).
        Paginates via Graph ``@odata.nextLink``.
        """
        params: dict[str, Any] = {
            "$select": _DEFAULT_SELECT,
            "$orderby": "receivedDateTime desc",
            "$top": str(top),
        }
        if since_iso:
            params["$filter"] = f"receivedDateTime ge {since_iso}"

        path = f"/users/{self._mbx(mailbox)}/mailFolders/inbox/messages"
        out: list[dict[str, Any]] = []
        next_url: str | None = None
        for page in range(max_pages):
            body = (
                self._request("GET", next_url)
                if next_url
                else self._request("GET", path, params=params)
            )
            batch = body.get("value", []) if isinstance(body, dict) else []
            out.extend(batch)
            next_url = body.get("@odata.nextLink") if isinstance(body, dict) else None
            log.info(
                "MS365: %s inbox page %d (%d msgs, %d total)",
                mailbox, page + 1, len(batch), len(out),
            )
            if not next_url:
                break
        return out

    def get_message_body(self, mailbox: str, message_id: str) -> dict[str, Any]:
        """Fetch a single message including its full body (text content)."""
        params = {"$select": f"{_DEFAULT_SELECT},body"}
        return self._request(
            "GET",
            f"/users/{self._mbx(mailbox)}/messages/{message_id}",
            params=params,
        )

    # ── Folder + message mutations (read/write scope) ─────────────────────

    def ensure_folder(self, mailbox: str, display_name: str) -> str:
        """Return the id of a top-level mail folder, creating it if absent."""
        cache_key = (mailbox, display_name)
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        # Graph $filter on displayName needs single-quote escaping (double them).
        escaped = display_name.replace("'", "''")
        found = self._request(
            "GET",
            f"/users/{self._mbx(mailbox)}/mailFolders",
            params={"$filter": f"displayName eq '{escaped}'", "$top": "10"},
        )
        existing = found.get("value", []) if isinstance(found, dict) else []
        if existing:
            folder_id = existing[0]["id"]
        else:
            created = self._request(
                "POST",
                f"/users/{self._mbx(mailbox)}/mailFolders",
                json_body={"displayName": display_name},
            )
            folder_id = created["id"]
            log.info("MS365: created folder %r in %s", display_name, mailbox)
        self._folder_cache[cache_key] = folder_id
        return folder_id

    def move_message(self, mailbox: str, message_id: str, destination_id: str) -> dict[str, Any]:
        """Move a message to another folder (returns the moved message)."""
        return self._request(
            "POST",
            f"/users/{self._mbx(mailbox)}/messages/{message_id}/move",
            json_body={"destinationId": destination_id},
        )

    def add_category(self, mailbox: str, message_id: str, category: str) -> dict[str, Any]:
        """Append a category tag to a message (idempotency marker for triage)."""
        msg = self.get_message_body(mailbox, message_id)
        categories = list(msg.get("categories") or [])
        if category not in categories:
            categories.append(category)
        return self._request(
            "PATCH",
            f"/users/{self._mbx(mailbox)}/messages/{message_id}",
            json_body={"categories": categories},
        )
