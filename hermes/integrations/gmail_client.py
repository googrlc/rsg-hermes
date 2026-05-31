"""Gmail (Google API) client: service account + domain-wide delegation.

Unattended server access to Workspace mailboxes. A service account with
domain-wide delegation authorized for the ``gmail.modify`` scope impersonates
each mailbox — no interactive OAuth, no per-user refresh tokens. ``google-auth``
mints/refreshes the access token (RS256 JWT → token exchange); the Gmail REST
calls themselves go through plain ``requests`` to match ``ms365_client``.

Scope is read + label/move only (``gmail.modify``) — there is **no** send
method here. Gmail has no folders, so the "quarantine folder" is a label
(``Hermes/Triage``); moving = add that label and remove ``INBOX``.

Requires the optional extra:  pip install -e '.[gmail]'
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailClientError(Exception):
    """Raised on auth failures or non-success Gmail API responses."""


class GmailClient:
    """Thin Gmail REST client backed by a delegated service account.

    One service-account key services every mailbox in the Workspace domain it
    is delegated for; ``mailbox`` (the impersonated subject) is passed to each
    call, and per-subject credentials are cached.
    """

    def __init__(self, key_path: str | None = None, timeout: float = 30.0) -> None:
        self.key_path = key_path or os.environ.get("GMAIL_SA_KEY_PATH", "")
        self.timeout = timeout
        # Cache delegated Credentials and resolved label ids per subject.
        self._creds: dict[str, Any] = {}
        self._label_cache: dict[tuple[str, str], str] = {}
        if not self.key_path:
            raise GmailClientError(
                "GMAIL_SA_KEY_PATH must be set (env or constructor) — path to the "
                "service-account JSON key."
            )
        if not os.path.exists(self.key_path):
            raise GmailClientError(f"GMAIL_SA_KEY_PATH not found: {self.key_path}")

    # ── Auth ──────────────────────────────────────────────────────────────

    def _credentials(self, subject: str):
        """Build (and cache) delegated credentials impersonating ``subject``."""
        if subject not in self._creds:
            try:
                from google.oauth2 import service_account  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise GmailClientError(
                    "google-auth not installed — run: pip install -e '.[gmail]'"
                ) from exc
            base = service_account.Credentials.from_service_account_file(
                self.key_path, scopes=SCOPES
            )
            self._creds[subject] = base.with_subject(subject)
        return self._creds[subject]

    def _token(self, subject: str) -> str:
        creds = self._credentials(subject)
        if not creds.valid:
            from google.auth.transport.requests import Request  # type: ignore

            creds.refresh(Request())
        return creds.token

    def _headers(self, subject: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token(subject)}",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    # ── Low-level verb ────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        subject: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = path if path.startswith("http") else f"{GMAIL_BASE}{path}"
        for attempt in range(2):
            extra = {"Content-Type": "application/json"} if json_body is not None else None
            resp = requests.request(
                method,
                url,
                headers=self._headers(subject, extra),
                params=params,
                json=json_body,
                timeout=self.timeout,
            )
            if resp.status_code == 401 and attempt == 0:
                log.info("Gmail: token rejected, refreshing for %s", subject)
                self._creds.pop(subject, None)
                continue
            if not resp.ok:
                raise GmailClientError(
                    f"Gmail {method} {path} failed {resp.status_code}: {resp.text[:500]}"
                )
            return resp.json() if resp.content else {}
        raise GmailClientError(f"Gmail {method} {path}: auth retry exhausted")

    # ── Reads ─────────────────────────────────────────────────────────────

    def list_inbox_messages(
        self,
        mailbox: str,
        *,
        after_epoch: int | None = None,
        exclude_label: str | None = None,
        max_results: int = 100,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """Return Inbox message metadata (id, From/Subject/List-Unsubscribe).

        ``after_epoch`` restricts to messages newer than a Unix timestamp;
        ``exclude_label`` (a label *name*) is filtered server-side via ``-label:``.
        Gmail's list endpoint returns only ids, so each id is fetched with
        ``format=metadata`` for the headers the classifier needs.
        """
        q_parts = ["in:inbox"]
        if after_epoch is not None:
            q_parts.append(f"after:{after_epoch}")
        if exclude_label:
            q_parts.append(f'-label:"{exclude_label}"')
        q = " ".join(q_parts)

        ids: list[str] = []
        page_token: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"q": q, "maxResults": str(max_results)}
            if page_token:
                params["pageToken"] = page_token
            body = self._request(
                "GET", f"/users/{mailbox}/messages", mailbox, params=params
            )
            ids.extend(m["id"] for m in body.get("messages", []))
            page_token = body.get("nextPageToken")
            if not page_token:
                break

        out: list[dict[str, Any]] = []
        for mid in ids:
            out.append(
                self._request(
                    "GET",
                    f"/users/{mailbox}/messages/{mid}",
                    mailbox,
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["From", "Subject", "List-Unsubscribe"],
                    },
                )
            )
        log.info("Gmail: %s inbox — %d messages", mailbox, len(out))
        return out

    def get_message_full(self, mailbox: str, message_id: str) -> dict[str, Any]:
        """Fetch a message with its full payload (for body extraction)."""
        return self._request(
            "GET",
            f"/users/{mailbox}/messages/{message_id}",
            mailbox,
            params={"format": "full"},
        )

    # ── Label + message mutations (gmail.modify scope) ────────────────────

    def ensure_label(self, mailbox: str, name: str) -> str:
        """Return the id of a label, creating it (nested via '/') if absent."""
        cache_key = (mailbox, name)
        if cache_key in self._label_cache:
            return self._label_cache[cache_key]

        existing = self._request("GET", f"/users/{mailbox}/labels", mailbox)
        for label in existing.get("labels", []):
            if label.get("name") == name:
                self._label_cache[cache_key] = label["id"]
                return label["id"]

        created = self._request(
            "POST",
            f"/users/{mailbox}/labels",
            mailbox,
            json_body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        log.info("Gmail: created label %r in %s", name, mailbox)
        self._label_cache[cache_key] = created["id"]
        return created["id"]

    def modify_message(
        self,
        mailbox: str,
        message_id: str,
        *,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add/remove labels. Removing INBOX moves a message out of the inbox."""
        return self._request(
            "POST",
            f"/users/{mailbox}/messages/{message_id}/modify",
            mailbox,
            json_body={
                "addLabelIds": add_label_ids or [],
                "removeLabelIds": remove_label_ids or [],
            },
        )

    # ── Parsing helpers ───────────────────────────────────────────────────

    @staticmethod
    def header(msg: dict[str, Any], name: str) -> str:
        """Case-insensitive lookup of a header value from a message payload."""
        headers = (msg.get("payload") or {}).get("headers") or []
        for h in headers:
            if h.get("name", "").lower() == name.lower():
                return h.get("value", "") or ""
        return ""

    @staticmethod
    def extract_text(msg: dict[str, Any]) -> str:
        """Best-effort plain-text body from a full message payload.

        Walks MIME parts for the first ``text/plain`` body; falls back to the
        API-provided ``snippet`` when no decodable text part exists.
        """
        payload = msg.get("payload") or {}

        def walk(part: dict[str, Any]) -> str | None:
            if part.get("mimeType") == "text/plain":
                data = (part.get("body") or {}).get("data")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
            for sub in part.get("parts", []) or []:
                found = walk(sub)
                if found:
                    return found
            return None

        return walk(payload) or msg.get("snippet", "") or ""
