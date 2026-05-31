"""Google Drive client — human-browsable mirror for the document library.

Reuses the Gmail service account (domain-wide delegation) with the Drive
scope. Creates a per-client folder tree under a root folder and uploads each
document as a Google Doc, returning the shareable web link.

Folder layout in the impersonated user's Drive:

    <root>/                         (HERMES_DRIVE_ROOT_FOLDER, default "Hermes Docs")
      <Client Account>/             (client space)
      _Internal References/
        <Freeform Folder>/          (internal space)

Requires the [gmail] extra (google-auth) and scope
``https://www.googleapis.com/auth/drive`` on the service account delegation.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"
SCOPES = ["https://www.googleapis.com/auth/drive"]
INTERNAL_ROOT = "_Internal References"


class GDriveClientError(Exception):
    """Raised on auth failures or non-success Drive responses."""


class GDriveClient:
    """Thin Drive REST client backed by a delegated service account."""

    def __init__(
        self,
        key_path: str | None = None,
        subject: str | None = None,
        root_folder: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.key_path = key_path or os.environ.get("GMAIL_SA_KEY_PATH", "")
        # The Drive owner — a real Google Workspace user (NOT the 365 mailbox).
        self.subject = (
            subject
            or os.environ.get("HERMES_DRIVE_SUBJECT")
            or os.environ.get("GMAIL_MAILBOXES", "").split(",")[0].strip()
        )
        self.root_folder = (
            root_folder or os.environ.get("HERMES_DRIVE_ROOT_FOLDER", "Hermes Docs")
        )
        self.timeout = timeout
        self._creds: Any = None
        self._folder_cache: dict[tuple[str, str], str] = {}
        if not self.key_path:
            raise GDriveClientError("GMAIL_SA_KEY_PATH must be set for the Drive mirror.")
        if not self.subject:
            raise GDriveClientError(
                "HERMES_DRIVE_SUBJECT (or GMAIL_MAILBOXES) must name the Drive owner."
            )

    # ── Auth ──────────────────────────────────────────────────────────────

    def _token(self) -> str:
        if self._creds is None:
            try:
                from google.oauth2 import service_account  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise GDriveClientError(
                    "google-auth not installed — run: pip install -e '.[gmail]'"
                ) from exc
            self._creds = service_account.Credentials.from_service_account_file(
                self.key_path, scopes=SCOPES
            ).with_subject(self.subject)
        if not self._creds.valid:
            from google.auth.transport.requests import Request  # type: ignore

            self._creds.refresh(Request())
        return self._creds.token

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        if extra:
            headers.update(extra)
        return headers

    # ── Folders ───────────────────────────────────────────────────────────

    def ensure_folder(self, name: str, parent_id: str | None = None) -> str:
        """Find (or create) a folder by name under ``parent_id`` (root if None)."""
        cache_key = (parent_id or "root", name)
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        q = (
            f"name = '{safe}' and mimeType = '{FOLDER_MIME}' and trashed = false "
            f"and '{parent_id or 'root'}' in parents"
        )
        r = requests.get(
            f"{DRIVE_API}/files",
            headers=self._headers(),
            params={"q": q, "fields": "files(id,name)", "spaces": "drive"},
            timeout=self.timeout,
        )
        if not r.ok:
            raise GDriveClientError(f"Drive folder search failed {r.status_code}: {r.text[:300]}")
        files = r.json().get("files", [])
        if files:
            folder_id = files[0]["id"]
        else:
            meta: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
            if parent_id:
                meta["parents"] = [parent_id]
            cr = requests.post(
                f"{DRIVE_API}/files",
                headers=self._headers({"Content-Type": "application/json"}),
                params={"fields": "id"},
                json=meta,
                timeout=self.timeout,
            )
            if not cr.ok:
                raise GDriveClientError(f"Drive folder create failed {cr.status_code}: {cr.text[:300]}")
            folder_id = cr.json()["id"]
            log.info("Drive: created folder %r", name)
        self._folder_cache[cache_key] = folder_id
        return folder_id

    def _folder_for(self, space: str, account_name: str | None, folder: str | None) -> str:
        root = self.ensure_folder(self.root_folder)
        if space == "client":
            return self.ensure_folder(account_name or "Unfiled", root)
        internal = self.ensure_folder(INTERNAL_ROOT, root)
        return self.ensure_folder(folder or "General", internal)

    # ── Upload ────────────────────────────────────────────────────────────

    def upload_document(
        self,
        *,
        space: str,
        account_name: str | None,
        folder: str | None,
        title: str,
        doc_type: str,
        content: str,
    ) -> dict[str, Any]:
        """Upload ``content`` as a Google Doc into the right folder.

        Returns the Drive file resource ({id, webViewLink, ...}).
        """
        parent = self._folder_for(space, account_name, folder)
        meta = {"name": title, "parents": [parent], "mimeType": DOC_MIME}
        boundary = "hermes-doc-boundary"
        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n"
            f"--{boundary}\r\n"
            "Content-Type: text/plain; charset=UTF-8\r\n\r\n"
            f"{content}\r\n"
            f"--{boundary}--"
        )
        r = requests.post(
            DRIVE_UPLOAD,
            headers=self._headers(
                {"Content-Type": f"multipart/related; boundary={boundary}"}
            ),
            params={"uploadType": "multipart", "fields": "id,webViewLink,name"},
            data=body.encode("utf-8"),
            timeout=self.timeout,
        )
        if not r.ok:
            raise GDriveClientError(f"Drive upload failed {r.status_code}: {r.text[:300]}")
        return r.json()
