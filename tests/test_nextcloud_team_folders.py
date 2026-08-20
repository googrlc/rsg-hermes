"""Team Folder path helpers + OCS setup planning (no live Nextcloud)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermes_integrations.nextcloud_client import NextcloudClient, NextcloudError
from hermes_integrations.nextcloud_team_folders import (
    ACCESS_GROUP,
    APP_ID,
    PERM_ALL,
    TEAM_FOLDER_NAME,
    NextcloudOcsClient,
    NextcloudOcsError,
    apply_setup,
    collect_status,
    format_status,
    plan_from_status,
)


def _resp(code, payload=None, text=""):
    r = MagicMock()
    r.status_code = code
    r.text = text
    r.json.return_value = payload if payload is not None else {}
    return r


def test_agency_account_dir_builds_lane_tree():
    nc = NextcloudClient(url="https://nc.example", user="hermes", app_password="pw")
    rel = nc.agency_account_dir(
        line="commercial",
        account="ABC Roofing",
        policy_type="General Liability",
        document_type="Declaration Pages",
        year="2027",
    )
    assert rel == "Commercial Lines/ABC Roofing/General Liability/Declaration Pages/2027"


def test_agency_account_dir_prefixes_base_path_on_ensure():
    sess = MagicMock()
    sess.request.return_value = _resp(201)
    nc = NextcloudClient(
        url="https://nc.example",
        user="hermes",
        app_password="pw",
        base_path="Agency Documents",
        session=sess,
    )
    stored = nc.ensure_agency_account_tree(
        line="personal",
        account="Smith Family",
        policy_type="Home",
        document_type="Applications",
        year="2026",
    )
    assert stored == (
        "Agency Documents/Personal Lines/Smith Family/Home/Applications/2026"
    )
    urls = [c.args[1] for c in sess.request.call_args_list]
    assert any("Agency%20Documents" in u and "Personal%20Lines" in u for u in urls)


def test_agency_account_dir_rejects_unknown_line():
    nc = NextcloudClient(url="https://nc.example", user="hermes", app_password="pw")
    with pytest.raises(NextcloudError, match="unknown agency line"):
        nc.agency_account_dir(
            line="life", account="X", policy_type="Term", document_type="Apps", year="2026"
        )


def test_files_ui_url_uses_dir_query():
    nc = NextcloudClient(url="https://nc.example", user="hermes", app_password="pw")
    url = nc.files_ui_url(
        "Commercial Lines/ABC Roofing/General Liability/Declaration Pages/2027",
        fileid=259,
    )
    assert url.startswith("https://nc.example/apps/files/?dir=/Commercial Lines/")
    assert "fileid=259" in url


def test_ocs_list_team_folders_none_when_app_missing():
    sess = MagicMock()
    sess.request.return_value = _resp(
        404,
        {"ocs": {"meta": {"status": "failure", "statuscode": 998, "message": "Invalid query"}},
         },
        "Invalid query",
    )
    ocs = NextcloudOcsClient(
        url="https://nc.example", user="hermes", app_password="pw", session=sess
    )
    with pytest.raises(NextcloudOcsError):
        ocs.request("GET", "/apps/groupfolders/folders")
    sess.request.return_value = _resp(
        404,
        {"ocs": {"meta": {"status": "failure", "statuscode": 998, "message": "Invalid query"}},
         },
        "Invalid query",
    )
    assert ocs.list_team_folders() is None


def test_plan_from_status_requires_admin_to_enable_app():
    status = {
        "team_folders_app_enabled": False,
        "groups": [ACCESS_GROUP, "admin"],
        "agency_documents_present": False,
        "is_admin": False,
    }
    plan = plan_from_status(status)
    enable = next(s for s in plan if s["action"] == "enable_app")
    create = next(s for s in plan if s["action"] == "create_team_folder")
    assert enable["needs"] == "admin"
    assert TEAM_FOLDER_NAME in create["detail"]
    assert any(s["action"] == "mkcol" and "Commercial Lines" in s["detail"] for s in plan)


def test_collect_status_reads_user_groups_and_dav():
    ocs = MagicMock()
    ocs.is_configured.return_value = True
    ocs.url = "https://nc.example"
    ocs.user = "hermes"
    ocs.current_user.return_value = {
        "id": "hermes",
        "displayname": "Hermes Agent",
        "groups": [ACCESS_GROUP],
        "subadmin": [ACCESS_GROUP],
    }
    ocs.list_groups.return_value = [ACCESS_GROUP, "admin"]
    ocs.list_users.return_value = ["Gretchen Coates", "Lamar", "hermes", "root"]
    ocs.group_members.return_value = ["Gretchen Coates", "Lamar", "hermes", "root"]
    ocs.list_team_folders.return_value = None
    dav = MagicMock()
    dav.is_configured.return_value = True
    dav.list_dir.return_value = [
        {"name": "Commercial Lines", "is_dir": True},
        {"name": "Personal Lines", "is_dir": True},
        {"name": "Clients", "is_dir": True},
    ]
    status = collect_status(ocs=ocs, dav=dav)
    assert status["is_admin"] is False
    assert status["team_folders_app_enabled"] is False
    assert status["agency_documents_present"] is False
    assert "Commercial Lines" in status["dav_root_folders"]
    assert status["service_account"] == "hermes"
    text = format_status(status, plan=plan_from_status(status))
    assert "Team Folders app enabled: False" in text
    assert ACCESS_GROUP in text


def test_apply_setup_refuses_non_admin_when_app_disabled():
    ocs = MagicMock()
    ocs.is_configured.return_value = True
    ocs.url = "https://nc.example"
    ocs.user = "hermes"
    ocs.current_user.return_value = {
        "id": "hermes",
        "groups": [ACCESS_GROUP],
        "subadmin": [ACCESS_GROUP],
    }
    ocs.list_groups.return_value = [ACCESS_GROUP]
    ocs.list_users.return_value = ["hermes"]
    ocs.group_members.return_value = ["hermes"]
    ocs.list_team_folders.return_value = None
    dav = MagicMock()
    dav.is_configured.return_value = True
    dav.list_dir.return_value = []
    with pytest.raises(NextcloudOcsError, match="not an admin"):
        apply_setup(ocs=ocs, dav=dav)


def test_apply_setup_creates_folder_and_lanes():
    ocs = MagicMock()
    ocs.is_configured.return_value = True
    ocs.url = "https://nc.example"
    ocs.user = "root"
    ocs.current_user.return_value = {"id": "root", "groups": ["admin"], "subadmin": []}
    ocs.list_groups.return_value = [ACCESS_GROUP, "admin"]
    ocs.list_users.return_value = ["root", "hermes"]
    ocs.group_members.return_value = ["root", "hermes"]
    ocs.list_team_folders.side_effect = [None, {}, {1: {"mount_point": TEAM_FOLDER_NAME}}]
    ocs.create_team_folder.return_value = 1
    dav = MagicMock()
    dav.is_configured.return_value = True
    dav.base_path = ""
    dav.list_dir.return_value = []
    dav._rel_with_base.side_effect = lambda p: p
    out = apply_setup(ocs=ocs, dav=dav, create_optional_groups=False)
    assert out["ok"] is True
    assert out["folder_id"] == 1
    ocs.enable_app.assert_called_once_with(APP_ID)
    ocs.create_team_folder.assert_called_once_with(TEAM_FOLDER_NAME)
    ocs.grant_group.assert_called_once_with(1, ACCESS_GROUP, PERM_ALL)
    assert dav.ensure_dirs.call_count == 3
    lanes = {c.args[0] for c in dav.ensure_dirs.call_args_list}
    assert lanes == {
        f"{TEAM_FOLDER_NAME}/Commercial Lines",
        f"{TEAM_FOLDER_NAME}/Personal Lines",
        f"{TEAM_FOLDER_NAME}/Claims",
    }
    assert out["set_env"]["NEXTCLOUD_BASE_PATH"] == TEAM_FOLDER_NAME
