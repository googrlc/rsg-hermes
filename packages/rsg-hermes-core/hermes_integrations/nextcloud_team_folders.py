"""Nextcloud Team Folders (groupfolders) admin + status helpers.

Team Folders are admin-owned shared mounts. Regular WebDAV MKCOL cannot create
one — that would land in the service user's personal storage, which is the
failure mode this module exists to avoid.

Config (env; never committed):
    NEXTCLOUD_URL
    NEXTCLOUD_USER / NEXTCLOUD_USERNAME
    NEXTCLOUD_APP_PASSWORD
    NEXTCLOUD_ADMIN_USER / NEXTCLOUD_ADMIN_APP_PASSWORD
        optional; required to enable the app, create groups, and create the
        Team Folder. The Hermes filing user is a subadmin of ``All Team`` and
        cannot do those steps.

Live RSG instance (2026-08-20): Nextcloud 34.0.2 at
https://nextcloud-x6wle-u69864.vm.elestio.app. Users ``Gretchen Coates``,
``Lamar``, ``hermes``, ``root``. Group ``All Team`` already contains everyone.
``hermes`` is the CRM/WebDAV service account. The ``groupfolders`` app is not
enabled until an admin runs ``scripts/nextcloud_team_folders_setup.py --apply``.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from hermes_integrations.nextcloud_client import (
    AGENCY_LINE_ROOTS,
    TEAM_FOLDER_NAME,
    NextcloudClient,
    NextcloudError,
)

if TYPE_CHECKING:
    import requests

# Existing live group that already contains every staff user + hermes + root.
ACCESS_GROUP = "All Team"

# Optional ACL subgroups. Created only when an admin applies the setup.
OPTIONAL_GROUPS = ("Commercial Lines", "Personal Lines", "Management")

# Nextcloud permission bits (lib/public/Constants.php).
PERM_READ = 1
PERM_UPDATE = 2
PERM_CREATE = 4
PERM_DELETE = 8
PERM_SHARE = 16
PERM_ALL = PERM_READ | PERM_UPDATE | PERM_CREATE | PERM_DELETE | PERM_SHARE  # 31

UNLIMITED_QUOTA = -3
DEFAULT_QUOTA_BYTES = 536870912000  # 500 GiB

APP_ID = "groupfolders"

# Staff mapping used when creating optional subgroups.
GROUP_MEMBERS: dict[str, tuple[str, ...]] = {
    "Commercial Lines": ("Lamar", "hermes"),
    "Personal Lines": ("Gretchen Coates", "hermes"),
    "Management": ("Lamar", "root"),
}


def _env_user() -> str:
    return (
        os.environ.get("NEXTCLOUD_ADMIN_USER")
        or os.environ.get("NEXTCLOUD_USER")
        or os.environ.get("NEXTCLOUD_USERNAME")
        or ""
    ).strip()


def _env_password(*, admin: bool) -> str:
    if admin:
        return (
            os.environ.get("NEXTCLOUD_ADMIN_APP_PASSWORD")
            or os.environ.get("NEXTCLOUD_APP_PASSWORD")
            or ""
        ).strip()
    return (os.environ.get("NEXTCLOUD_APP_PASSWORD") or "").strip()


class NextcloudOcsError(NextcloudError):
    pass


class NextcloudOcsClient:
    """Thin OCS client. Admin-gated routes 403 for the Hermes filing user."""

    def __init__(
        self,
        *,
        url: str | None = None,
        user: str | None = None,
        app_password: str | None = None,
        session: "requests.Session | None" = None,
        verify_tls: bool | None = None,
    ) -> None:
        self.url = (url if url is not None else os.environ.get("NEXTCLOUD_URL", "")).strip().rstrip("/")
        self.user = (user if user is not None else _env_user()).strip()
        self.app_password = (
            app_password if app_password is not None else _env_password(admin=True)
        ).strip()
        if verify_tls is None:
            verify_tls = os.environ.get("HERMES_VERIFY_TLS", "false").strip().lower() in (
                "1",
                "true",
                "yes",
            )
        self.verify_tls = verify_tls
        self._session = session

    def is_configured(self) -> bool:
        return bool(self.url and self.user and self.app_password)

    def _require(self) -> None:
        if not self.is_configured():
            raise NextcloudOcsError(
                "Nextcloud is not configured — set NEXTCLOUD_URL, NEXTCLOUD_USER, "
                "and NEXTCLOUD_APP_PASSWORD."
            )

    @property
    def session(self) -> "requests.Session":
        if self._session is None:
            import requests

            self._session = requests.Session()
            self._session.auth = (self.user, self.app_password)
        return self._session

    def request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an OCS or app route. ``path`` starts with /ocs/ or /apps/."""
        self._require()
        url = f"{self.url}{path}"
        sep = "&" if "?" in path else "?"
        if "format=json" not in path:
            url = f"{url}{sep}format=json"
        headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
        resp = self.session.request(
            method,
            url,
            data=data,
            json=json_body,
            headers=headers,
            verify=self.verify_tls,
            timeout=30,
        )
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text[:500]}
        if resp.status_code >= 400:
            meta = (payload.get("ocs") or {}).get("meta") or {}
            message = meta.get("message") or resp.text[:200]
            raise NextcloudOcsError(
                f"{method} {path} failed: {resp.status_code} {message}"
            )
        return payload

    def current_user(self) -> dict[str, Any]:
        data = self.request("GET", "/ocs/v2.php/cloud/user")
        return (data.get("ocs") or {}).get("data") or {}

    def list_users(self) -> list[str]:
        data = self.request("GET", "/ocs/v1.php/cloud/users")
        users = ((data.get("ocs") or {}).get("data") or {}).get("users") or []
        return list(users)

    def list_groups(self) -> list[str]:
        data = self.request("GET", "/ocs/v1.php/cloud/groups")
        groups = ((data.get("ocs") or {}).get("data") or {}).get("groups") or []
        return list(groups)

    def group_members(self, group: str) -> list[str]:
        data = self.request("GET", f"/ocs/v2.php/cloud/groups/{quote(group, safe='')}")
        return list(((data.get("ocs") or {}).get("data") or {}).get("users") or [])

    def create_group(self, group: str) -> None:
        self.request("POST", "/ocs/v2.php/cloud/groups", data={"groupid": group})

    def add_user_to_group(self, user: str, group: str) -> None:
        self.request(
            "POST",
            f"/ocs/v2.php/cloud/users/{quote(user, safe='')}/groups",
            data={"groupid": group},
        )

    def enable_app(self, app_id: str = APP_ID) -> None:
        self.request("POST", f"/ocs/v2.php/cloud/apps/{quote(app_id, safe='')}")

    def list_team_folders(self) -> dict[str, Any] | None:
        """Return groupfolders data. ``None`` means the app is not enabled (404)."""
        try:
            data = self.request("GET", f"/apps/{APP_ID}/folders")
        except NextcloudOcsError as exc:
            if "404" in str(exc) or "998" in str(exc):
                return None
            raise
        inner = (data.get("ocs") or {}).get("data")
        if inner is None:
            return {}
        if isinstance(inner, dict):
            return inner
        if isinstance(inner, list):
            return {str(i): v for i, v in enumerate(inner)}
        return {}

    def create_team_folder(self, mountpoint: str) -> int:
        data = self.request(
            "POST",
            f"/apps/{APP_ID}/folders",
            data={"mountpoint": mountpoint},
        )
        folder_id = (data.get("ocs") or {}).get("data")
        if isinstance(folder_id, dict):
            folder_id = folder_id.get("id")
        if folder_id is None:
            raise NextcloudOcsError(f"create team folder returned no id: {data!r}")
        return int(folder_id)

    def grant_group(self, folder_id: int, group: str, permissions: int = PERM_ALL) -> None:
        self.request(
            "POST",
            f"/apps/{APP_ID}/folders/{folder_id}/groups",
            data={"group": group},
        )
        self.request(
            "POST",
            f"/apps/{APP_ID}/folders/{folder_id}/groups/{quote(group, safe='')}",
            data={"permissions": permissions},
        )

    def set_quota(self, folder_id: int, quota_bytes: int) -> None:
        self.request(
            "POST",
            f"/apps/{APP_ID}/folders/{folder_id}/quota",
            data={"quota": quota_bytes},
        )


def collect_status(
    ocs: NextcloudOcsClient | None = None,
    dav: NextcloudClient | None = None,
) -> dict[str, Any]:
    """Read-only picture of the live Nextcloud Team Folder setup."""
    ocs = ocs or NextcloudOcsClient(
        user=os.environ.get("NEXTCLOUD_USER") or os.environ.get("NEXTCLOUD_USERNAME"),
        app_password=os.environ.get("NEXTCLOUD_APP_PASSWORD"),
    )
    dav = dav or NextcloudClient()
    if not ocs.is_configured():
        raise NextcloudOcsError("Nextcloud is not configured")

    user = ocs.current_user()
    groups: list[str] = []
    users: list[str] = []
    access_members: list[str] = []
    groups_error = None
    try:
        groups = ocs.list_groups()
        users = ocs.list_users()
        if ACCESS_GROUP in groups:
            access_members = ocs.group_members(ACCESS_GROUP)
    except NextcloudOcsError as exc:
        groups_error = str(exc)

    team_folders: dict[str, Any] | None = None
    team_folders_error = None
    try:
        team_folders = ocs.list_team_folders()
    except NextcloudOcsError as exc:
        team_folders_error = str(exc)

    dav_root: list[str] = []
    dav_error = None
    if dav.is_configured():
        try:
            dav_root = [e["name"] for e in dav.list_dir("") if e.get("is_dir")]
        except NextcloudError as exc:
            dav_error = str(exc)

    folder_values = (team_folders or {}).values() if isinstance(team_folders, dict) else []
    agency_present = TEAM_FOLDER_NAME in dav_root or any(
        str((info or {}).get("mount_point") or (info or {}).get("mountpoint") or "")
        == TEAM_FOLDER_NAME
        for info in folder_values
        if isinstance(info, dict)
    )
    app_enabled = team_folders is not None

    return {
        "url": ocs.url,
        "auth_user": ocs.user,
        "current_user": {
            "id": user.get("id"),
            "displayname": user.get("displayname") or user.get("display-name"),
            "groups": user.get("groups") or [],
            "subadmin": user.get("subadmin") or [],
        },
        "is_admin": "admin" in (user.get("groups") or []),
        "users": users,
        "groups": groups,
        "access_group": ACCESS_GROUP,
        "access_group_members": access_members,
        "groups_error": groups_error,
        "team_folders_app_enabled": app_enabled,
        "team_folders": team_folders,
        "team_folders_error": team_folders_error,
        "dav_root_folders": dav_root,
        "dav_error": dav_error,
        "agency_documents_present": agency_present,
        "service_account": "hermes",
        "recommended_base_path": TEAM_FOLDER_NAME,
        "lane_roots": dict(AGENCY_LINE_ROOTS),
    }


def plan_from_status(status: dict[str, Any]) -> list[dict[str, str]]:
    """Human-readable apply plan. Each item is {action, detail, needs}."""
    steps: list[dict[str, str]] = []
    if not status.get("team_folders_app_enabled"):
        steps.append({
            "action": "enable_app",
            "detail": "Enable the Team Folders app (id: groupfolders)",
            "needs": "admin",
        })
    else:
        steps.append({
            "action": "enable_app",
            "detail": "Team Folders app already enabled",
            "needs": "none",
        })

    existing_groups = set(status.get("groups") or [])
    for group in OPTIONAL_GROUPS:
        if group in existing_groups:
            steps.append({
                "action": "create_group",
                "detail": f"Group {group!r} already exists",
                "needs": "none",
            })
        else:
            steps.append({
                "action": "create_group",
                "detail": f"Create group {group!r} for optional subfolder ACLs",
                "needs": "admin",
            })

    if status.get("agency_documents_present"):
        steps.append({
            "action": "create_team_folder",
            "detail": f"{TEAM_FOLDER_NAME!r} already visible",
            "needs": "none",
        })
    else:
        steps.append({
            "action": "create_team_folder",
            "detail": (
                f"Create Team Folder {TEAM_FOLDER_NAME!r}, grant {ACCESS_GROUP!r} "
                "full permissions, set 500 GiB quota"
            ),
            "needs": "admin",
        })

    for lane in AGENCY_LINE_ROOTS.values():
        steps.append({
            "action": "mkcol",
            "detail": f"Ensure {TEAM_FOLDER_NAME}/{lane}",
            "needs": "webdav",
        })
    return steps


def apply_setup(
    *,
    ocs: NextcloudOcsClient | None = None,
    dav: NextcloudClient | None = None,
    quota_bytes: int = DEFAULT_QUOTA_BYTES,
    create_optional_groups: bool = True,
) -> dict[str, Any]:
    """Enable Team Folders, create Agency Documents, grant All Team, MKCOL lanes.

    Requires a Nextcloud admin. Raises NextcloudOcsError on 403/404 with the
    remaining manual step in the message.
    """
    ocs = ocs or NextcloudOcsClient()
    dav = dav or NextcloudClient()
    status = collect_status(ocs=ocs, dav=dav)
    results: list[dict[str, Any]] = []

    if not status.get("is_admin") and not status.get("team_folders_app_enabled"):
        raise NextcloudOcsError(
            "Logged-in Nextcloud user "
            f"{status['auth_user']!r} is not an admin and Team Folders is not "
            "enabled. Log in as the Elestio admin user `root`, enable Apps → "
            "Team folders, generate an app password, then re-run with "
            "NEXTCLOUD_ADMIN_USER=root and NEXTCLOUD_ADMIN_APP_PASSWORD set. "
            f"Existing access group {ACCESS_GROUP!r} already has members "
            f"{status.get('access_group_members')}. Service account is `hermes` "
            "(do not create agency_bot)."
        )

    if not status.get("team_folders_app_enabled"):
        ocs.enable_app(APP_ID)
        results.append({"action": "enable_app", "status": "enabled", "id": APP_ID})
    else:
        results.append({"action": "enable_app", "status": "existing", "id": APP_ID})

    if create_optional_groups:
        existing = set(ocs.list_groups())
        for group in OPTIONAL_GROUPS:
            if group in existing:
                results.append({"action": "create_group", "status": "existing", "group": group})
                continue
            ocs.create_group(group)
            for member in GROUP_MEMBERS.get(group, ()):
                try:
                    ocs.add_user_to_group(member, group)
                except NextcloudOcsError:
                    results.append({
                        "action": "add_user_to_group",
                        "status": "skipped",
                        "user": member,
                        "group": group,
                    })
            results.append({"action": "create_group", "status": "created", "group": group})

    folders = ocs.list_team_folders() or {}
    folder_id = None
    for fid, info in folders.items():
        mount = ""
        if isinstance(info, dict):
            mount = str(info.get("mount_point") or info.get("mountpoint") or "")
        if mount == TEAM_FOLDER_NAME:
            folder_id = int(fid)
            break
    if folder_id is None:
        folder_id = ocs.create_team_folder(TEAM_FOLDER_NAME)
        results.append({
            "action": "create_team_folder",
            "status": "created",
            "id": folder_id,
            "mountpoint": TEAM_FOLDER_NAME,
        })
    else:
        results.append({
            "action": "create_team_folder",
            "status": "existing",
            "id": folder_id,
            "mountpoint": TEAM_FOLDER_NAME,
        })

    ocs.grant_group(folder_id, ACCESS_GROUP, PERM_ALL)
    results.append({
        "action": "grant_group",
        "status": "ok",
        "group": ACCESS_GROUP,
        "permissions": PERM_ALL,
    })
    ocs.set_quota(folder_id, quota_bytes)
    results.append({"action": "set_quota", "status": "ok", "quota_bytes": quota_bytes})

    if not dav.is_configured():
        raise NextcloudOcsError("WebDAV client is not configured; cannot MKCOL lane folders")
    created_dirs = []
    for lane in AGENCY_LINE_ROOTS.values():
        rel = f"{TEAM_FOLDER_NAME}/{lane}"
        # If NEXTCLOUD_BASE_PATH is already Agency Documents, don't double-prefix.
        if (dav.base_path or "") == TEAM_FOLDER_NAME:
            rel = lane
        dav.ensure_dirs(rel)
        created_dirs.append(dav._rel_with_base(rel))
    results.append({"action": "mkcol", "status": "ok", "paths": created_dirs})

    return {
        "ok": True,
        "folder_id": folder_id,
        "mountpoint": TEAM_FOLDER_NAME,
        "access_group": ACCESS_GROUP,
        "results": results,
        "set_env": {"NEXTCLOUD_BASE_PATH": TEAM_FOLDER_NAME},
        "crm_dav_prefix": f"/remote.php/dav/files/hermes/{TEAM_FOLDER_NAME}/",
        "crm_files_ui_prefix": f"{ocs.url}/apps/files/?dir=/{TEAM_FOLDER_NAME}",
    }


def format_status(status: dict[str, Any], *, plan: list[dict[str, str]] | None = None) -> str:
    """Pretty-print status for CLI / artifacts. No secrets."""
    lines = [
        f"Nextcloud: {status.get('url')}",
        f"Auth user: {status.get('auth_user')}  admin={status.get('is_admin')}",
        f"Display: {((status.get('current_user') or {}).get('displayname'))}",
        f"Groups ({status.get('auth_user')}): {(status.get('current_user') or {}).get('groups')}",
        f"Subadmin of: {(status.get('current_user') or {}).get('subadmin')}",
        f"Users: {status.get('users')}",
        f"Groups: {status.get('groups')}",
        f"{ACCESS_GROUP} members: {status.get('access_group_members')}",
        f"Team Folders app enabled: {status.get('team_folders_app_enabled')}",
        f"Team folders: {json.dumps(status.get('team_folders') or {}, sort_keys=True)}",
        f"DAV root folders: {status.get('dav_root_folders')}",
        f"{TEAM_FOLDER_NAME} present: {status.get('agency_documents_present')}",
        f"Service account: {status.get('service_account')}",
        f"Recommended NEXTCLOUD_BASE_PATH: {status.get('recommended_base_path')}",
    ]
    if status.get("groups_error"):
        lines.append(f"Groups error: {status['groups_error']}")
    if status.get("team_folders_error"):
        lines.append(f"Team folders error: {status['team_folders_error']}")
    if status.get("dav_error"):
        lines.append(f"DAV error: {status['dav_error']}")
    if plan is not None:
        lines.append("Plan:")
        for i, step in enumerate(plan, 1):
            lines.append(f"  {i}. [{step['needs']}] {step['action']}: {step['detail']}")
    return "\n".join(lines)
