"""Microsoft SharePoint read client via Graph API (app-only auth).

Uses the same Entra app registration as mail triage (``MS365_*`` env vars).
SharePoint reads require **application** permissions such as ``Sites.Read.All``
and ``Files.Read.All`` (admin-consented), in addition to any mail permissions.

Default site scope comes from ``SHAREPOINT_SITE_URL``, e.g.
``https://contoso.sharepoint.com/sites/RSG-Knowledge``.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from hermes_integrations.ms365_client import MS365Client, MS365ClientError

log = logging.getLogger(__name__)

_SITE_URL_RE = re.compile(
    r"^https?://(?P<host>[^/]+)(?P<path>/.*)?$",
    re.IGNORECASE,
)
_MAX_READ_BYTES = int(os.environ.get("SHAREPOINT_MAX_READ_BYTES", "524288"))


class SharePointClient(MS365Client):
    """Graph client scoped to one SharePoint site (optional default)."""

    def __init__(
        self,
        *,
        site_url: str | None = None,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            timeout=timeout,
        )
        self.default_site_url = (site_url or os.environ.get("SHAREPOINT_SITE_URL", "")).strip()
        self._default_site: dict[str, Any] | None = None

    @staticmethod
    def parse_site_url(site_url: str) -> tuple[str, str]:
        """Return (hostname, site_path) for a SharePoint site URL."""
        url = site_url.strip().rstrip("/")
        match = _SITE_URL_RE.match(url)
        if not match:
            raise MS365ClientError(
                f"Invalid SharePoint site URL {site_url!r} — expected "
                "https://tenant.sharepoint.com/sites/YourSite"
            )
        host = match.group("host")
        path = match.group("path") or "/"
        if not path.startswith("/"):
            path = "/" + path
        return host, path

    def list_sites(self, query: str = "*", *, limit: int = 50) -> list[dict[str, Any]]:
        """Search SharePoint sites in the tenant (for consolidation inventory)."""
        q = (query or "*").strip() or "*"
        body = self._request(
            "GET",
            "/sites",
            params={"search": q, "$top": str(max(1, min(limit, 100)))},
        )
        return body.get("value", []) if isinstance(body, dict) else []

    def get_site(self, site_url: str | None = None) -> dict[str, Any]:
        """Resolve a site URL to a Graph site resource (includes ``id``)."""
        url = (site_url or self.default_site_url).strip()
        if not url:
            raise MS365ClientError(
                "SharePoint site URL required — pass site_url or set SHAREPOINT_SITE_URL."
            )
        host, path = self.parse_site_url(url)
        return self._request("GET", f"/sites/{host}:{path}")

    def default_site(self) -> dict[str, Any]:
        if self._default_site is None:
            self._default_site = self.get_site()
        return self._default_site

    def list_drives(self, site_id: str | None = None) -> list[dict[str, Any]]:
        site = site_id or self.default_site()["id"]
        body = self._request("GET", f"/sites/{site}/drives")
        return body.get("value", []) if isinstance(body, dict) else []

    def default_drive(self) -> dict[str, Any]:
        drives = self.list_drives()
        if not drives:
            raise MS365ClientError("No document libraries found on the configured site.")
        for drive in drives:
            name = (drive.get("name") or "").lower()
            if name in ("documents", "shared documents"):
                return drive
        return drives[0]

    def list_folder(
        self,
        folder_path: str = "/",
        *,
        drive_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List children under a folder path (``/`` = library root)."""
        drive = drive_id or self.default_drive()["id"]
        path = (folder_path or "/").strip()
        if path in ("", "/"):
            body = self._request("GET", f"/drives/{drive}/root/children")
        else:
            clean = path.strip("/")
            body = self._request(
                "GET",
                f"/drives/{drive}/root:/{clean}:/children",
            )
        return body.get("value", []) if isinstance(body, dict) else []

    def search_files(
        self,
        query: str,
        *,
        limit: int = 25,
        site_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search file names and content under the default site drive."""
        q = (query or "").strip()
        if not q:
            raise MS365ClientError("search query is required")
        site = site_id or self.default_site()["id"]
        drive = self.default_drive()["id"]
        # Drive-scoped search keeps results inside the knowledge library.
        body = self._request(
            "GET",
            f"/drives/{drive}/root/search(q='{q.replace(chr(39), chr(39)*2)}')",
            params={"$top": str(max(1, min(limit, 50)))},
        )
        hits = body.get("value", []) if isinstance(body, dict) else []
        if hits:
            return hits
        # Fallback: site-wide query when drive search returns nothing.
        fallback = self._request(
            "GET",
            "/search/query",
            json_body={
                "requests": [
                    {
                        "entityTypes": ["driveItem"],
                        "query": {"queryString": q},
                        "from": 0,
                        "size": max(1, min(limit, 50)),
                        "sharePointOneDriveScope": {"siteId": site},
                    }
                ]
            },
        )
        containers = fallback.get("value", []) if isinstance(fallback, dict) else []
        if not containers:
            return []
        hits_container = containers[0].get("hitsContainers") or []
        if not hits_container:
            return []
        out: list[dict[str, Any]] = []
        for hit in hits_container[0].get("hits") or []:
            resource = hit.get("resource") or {}
            if resource:
                out.append(resource)
        return out

    def read_item_text(
        self,
        item_id: str,
        *,
        drive_id: str | None = None,
    ) -> str:
        """Download a drive item and return UTF-8 text when reasonable."""
        if not item_id:
            raise MS365ClientError("item_id is required")
        drive = drive_id or self.default_drive()["id"]
        meta = self._request("GET", f"/drives/{drive}/items/{item_id}")
        name = str(meta.get("name") or "")
        size = int(meta.get("size") or 0)
        if size > _MAX_READ_BYTES:
            raise MS365ClientError(
                f"File {name!r} is {size} bytes — max {_MAX_READ_BYTES} for read_document."
            )
        mime = ""
        file_meta = meta.get("file") or {}
        if isinstance(file_meta, dict):
            mime = str(file_meta.get("mimeType") or "")

        import requests

        url = f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{item_id}/content"
        resp = requests.get(url, headers=self._headers(), timeout=self.timeout)
        if resp.status_code == 401:
            self._authenticate()
            resp = requests.get(url, headers=self._headers(), timeout=self.timeout)
        if not resp.ok:
            raise MS365ClientError(
                f"download failed {resp.status_code}: {resp.text[:300]}"
            )
        raw = resp.content
        text_types = (
            "text/",
            "application/json",
            "application/xml",
            "application/markdown",
        )
        if mime.startswith(text_types) or name.lower().endswith(
            (".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".html", ".htm")
        ):
            return raw.decode("utf-8", errors="replace")
        raise MS365ClientError(
            f"File {name!r} ({mime or 'unknown type'}) is not a plain-text document. "
            "Use list/search to find a markdown or text file."
        )

    def ping(self) -> str:
        """Auth check plus optional default site resolution."""
        self._authenticate()
        if self.default_site_url:
            site = self.default_site()
            return (
                f"SharePoint reachable. site={site.get('displayName') or site.get('name')} "
                f"webUrl={site.get('webUrl')}"
            )
        return "SharePoint Graph auth OK (SHAREPOINT_SITE_URL not set — site-scoped tools need it)."
