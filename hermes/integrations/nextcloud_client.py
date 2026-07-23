"""General Nextcloud document store over WebDAV.

RSG's agency documents (COIs, policies, proposals, correspondence, renewal
reviews) live in Nextcloud. This client files ANY document, not just renewals,
so it is the shared backend for the renewal PDF filer, the COI/ACORD flow, and
the document library.

Config (env; real values live only in .env / 1Password, never committed):
    NEXTCLOUD_URL           https://host            (base, no trailing /remote.php)
    NEXTCLOUD_USER          filing account (e.g. root)
    NEXTCLOUD_APP_PASSWORD  Nextcloud app password
    NEXTCLOUD_BASE_PATH     optional prefix under the user's files (e.g. "Agency")

Folder taxonomy (confirmed):
    Clients/{client}/{Renewal Reviews|COIs|Policies|Proposals|Quotes|Correspondence}/
    Internal/{folder}/
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote

if TYPE_CHECKING:
    import requests

# The confirmed per-client document categories.
CLIENT_CATEGORIES = ("Renewal Reviews", "COIs", "Policies", "Proposals", "Quotes", "Correspondence")

# PROPFIND body — ask only for the props list_dir surfaces (keeps the response small).
_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:resourcetype/><d:getcontentlength/><d:getlastmodified/><d:displayname/>"
    "</d:prop></d:propfind>"
)
DEFAULT_CATEGORY = "Renewal Reviews"
QUOTES_CATEGORY = "Quotes"


class NextcloudError(RuntimeError):
    pass


def _sanitize_segment(name: str) -> str:
    """Trim a path segment to something safe for a folder/file name (no slashes)."""
    cleaned = (name or "").replace("/", "-").replace("\\", "-").strip().strip(".")
    return cleaned or "unnamed"


class NextcloudClient:
    def __init__(
        self,
        *,
        url: str | None = None,
        user: str | None = None,
        app_password: str | None = None,
        base_path: str | None = None,
        session: "requests.Session | None" = None,
        verify_tls: bool | None = None,
    ) -> None:
        self.url = (url if url is not None else os.environ.get("NEXTCLOUD_URL", "")).strip().rstrip("/")
        # Accept either NEXTCLOUD_USER or the box's existing NEXTCLOUD_USERNAME.
        env_user = os.environ.get("NEXTCLOUD_USER") or os.environ.get("NEXTCLOUD_USERNAME", "")
        self.user = (user if user is not None else env_user).strip()
        self.app_password = (
            app_password if app_password is not None else os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
        ).strip()
        self.base_path = (
            base_path if base_path is not None else os.environ.get("NEXTCLOUD_BASE_PATH", "")
        ).strip().strip("/")
        if verify_tls is None:
            verify_tls = os.environ.get("HERMES_VERIFY_TLS", "false").strip().lower() in ("1", "true", "yes")
        self.verify_tls = verify_tls
        self._session = session

    # -- config / plumbing --------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self.url and self.user and self.app_password)

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise NextcloudError(
                "Nextcloud is not configured — set NEXTCLOUD_URL, NEXTCLOUD_USER, and "
                "NEXTCLOUD_APP_PASSWORD."
            )

    @property
    def session(self) -> "requests.Session":
        if self._session is None:
            import requests

            self._session = requests.Session()
            self._session.auth = (self.user, self.app_password)
        return self._session

    def _dav_base(self) -> str:
        return f"{self.url}/remote.php/dav/files/{quote(self.user)}"

    def _rel_with_base(self, rel_path: str) -> str:
        rel = rel_path.strip("/")
        return f"{self.base_path}/{rel}" if self.base_path else rel

    def _encode(self, rel_path: str) -> str:
        return "/".join(quote(seg) for seg in rel_path.split("/") if seg != "")

    def _dav_url(self, rel_path: str) -> str:
        return f"{self._dav_base()}/{self._encode(self._rel_with_base(rel_path))}"

    # -- operations ---------------------------------------------------------

    def ensure_dirs(self, rel_dir: str) -> None:
        """MKCOL each ancestor folder (idempotent — 405/301 'exists' is fine)."""
        self._require_configured()
        full = self._rel_with_base(rel_dir).strip("/")
        parts = [p for p in full.split("/") if p]
        acc = ""
        for part in parts:
            acc = f"{acc}/{part}" if acc else part
            url = f"{self._dav_base()}/{self._encode(acc)}"
            resp = self.session.request("MKCOL", url, verify=self.verify_tls, timeout=30)
            # 201 created; 405 already exists; 301/302 also treated as present.
            if resp.status_code not in (201, 405, 301, 302):
                raise NextcloudError(f"MKCOL {acc} failed: {resp.status_code} {resp.text[:200]}")

    # -- Talk (chat) --------------------------------------------------------

    def post_talk_message(self, token: str, message: str) -> None:
        """Post a message to a Nextcloud Talk conversation (spreed OCS API).

        ``token`` is the conversation token (from the room URL …/call/<token>).
        The service user must be a participant of that room.
        """
        self._require_configured()
        if not token:
            raise NextcloudError("Talk conversation token is required (set NEXTCLOUD_TALK_TOKEN).")
        url = f"{self.url}/ocs/v2.php/apps/spreed/api/v1/chat/{quote(token)}"
        resp = self.session.post(
            url,
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            data={"message": message},
            verify=self.verify_tls,
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            raise NextcloudError(f"Talk post to {token} failed: {resp.status_code} {resp.text[:200]}")

    def put_file(self, rel_path: str, content: bytes, *, content_type: str = "application/octet-stream") -> str:
        """Upload bytes to *rel_path* (creating parent dirs). Returns the stored path."""
        self._require_configured()
        parent = "/".join(rel_path.strip("/").split("/")[:-1])
        if parent:
            self.ensure_dirs(parent)
        resp = self.session.put(
            self._dav_url(rel_path),
            data=content,
            headers={"Content-Type": content_type},
            verify=self.verify_tls,
            timeout=60,
        )
        if resp.status_code not in (200, 201, 204):
            raise NextcloudError(f"PUT {rel_path} failed: {resp.status_code} {resp.text[:200]}")
        return self._rel_with_base(rel_path)

    def ensure_client_folders(self, client: str) -> str:
        """Create the standard Clients/{client}/{category}/ folder tree. Returns the client base path."""
        self._require_configured()
        base = f"Clients/{_sanitize_segment(client)}"
        for category in CLIENT_CATEGORIES:
            self.ensure_dirs(f"{base}/{category}")
        return self._rel_with_base(base)

    # -- read operations (client-360) ---------------------------------------
    # The CRM Desk assistant reads client documents here. Read-only: PROPFIND to
    # list a folder, GET to fetch a file's bytes. Neither creates or mutates.

    def list_dir(self, rel_path: str) -> list[dict[str, Any]]:
        """List the immediate children of a folder via WebDAV PROPFIND (Depth 1).

        Returns ``[{name, path, is_dir, size, modified}]`` for each child (the
        folder itself is omitted). ``path`` is relative to ``base_path`` — the
        same form ``read_file``/``put_file`` accept. Returns ``[]`` if the folder
        is missing (404). Read-only.
        """
        self._require_configured()
        import xml.etree.ElementTree as ET

        url = self._dav_url(rel_path)
        resp = self.session.request(
            "PROPFIND", url,
            headers={"Depth": "1", "Content-Type": "application/xml"},
            data=_PROPFIND_BODY,
            verify=self.verify_tls, timeout=30,
        )
        if resp.status_code == 404:
            return []
        if resp.status_code not in (207, 200):
            raise NextcloudError(f"PROPFIND {rel_path} failed: {resp.status_code} {resp.text[:200]}")

        # The requested folder's own DAV path (used to drop the self entry).
        # Compare on the UNencoded path — hrefs are unquoted below.
        parent_rel = self._rel_with_base(rel_path).strip("/")
        self_dav = "/" + parent_rel
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:  # noqa: BLE001
            raise NextcloudError(f"PROPFIND {rel_path}: bad XML ({exc})")

        ns = {"d": "DAV:"}
        out: list[dict[str, Any]] = []
        for resp_el in root.findall("d:response", ns):
            href_el = resp_el.find("d:href", ns)
            if href_el is None or not href_el.text:
                continue
            href = unquote(href_el.text)
            # strip the DAV base so we compare on the files path only
            dav_path = href.split("/remote.php/dav/files/", 1)[-1]
            # dav_path is "<user>/<...>"; drop the leading user segment
            dav_path = "/" + dav_path.split("/", 1)[-1].strip("/") if "/" in dav_path else "/"
            if dav_path.rstrip("/") == self_dav.rstrip("/"):
                continue  # the folder itself
            prop = resp_el.find("d:propstat/d:prop", ns)
            is_dir = prop is not None and prop.find("d:resourcetype/d:collection", ns) is not None
            name = href.rstrip("/").rsplit("/", 1)[-1]
            child_full = dav_path.strip("/")
            child_rel = child_full[len(parent_rel):].strip("/") if parent_rel and child_full.startswith(parent_rel) else name
            rel_out = f"{rel_path.strip('/')}/{child_rel}".strip("/")
            size_el = prop.find("d:getcontentlength", ns) if prop is not None else None
            mod_el = prop.find("d:getlastmodified", ns) if prop is not None else None
            out.append({
                "name": name,
                "path": rel_out,
                "is_dir": bool(is_dir),
                "size": int(size_el.text) if (size_el is not None and (size_el.text or "").isdigit()) else None,
                "modified": mod_el.text if mod_el is not None else None,
            })
        return out

    def read_file(self, rel_path: str) -> bytes:
        """Download a file's bytes via WebDAV GET. Read-only. Raises
        ``NextcloudError`` on 404 or any non-2xx."""
        self._require_configured()
        resp = self.session.get(self._dav_url(rel_path), verify=self.verify_tls, timeout=60)
        if resp.status_code == 404:
            raise NextcloudError(f"Not found: {rel_path}")
        if not resp.ok:
            raise NextcloudError(f"GET {rel_path} failed: {resp.status_code} {resp.text[:200]}")
        return resp.content

    def file_document(
        self,
        *,
        content: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
        client: str | None = None,
        category: str = DEFAULT_CATEGORY,
        internal_folder: str | None = None,
    ) -> dict[str, Any]:
        """File any document. Returns ``{"path": ..., "url": ...}``.

        ``client`` -> Clients/{client}/{category}/; else ``internal_folder`` ->
        Internal/{folder}/; else Internal/General/.
        """
        fname = _sanitize_segment(filename)
        if client:
            rel = f"Clients/{_sanitize_segment(client)}/{_sanitize_segment(category)}/{fname}"
        elif internal_folder:
            rel = f"Internal/{_sanitize_segment(internal_folder)}/{fname}"
        else:
            rel = f"Internal/General/{fname}"
        stored = self.put_file(rel, content, content_type=content_type)
        return {"path": stored, "url": self._dav_url(rel)}
