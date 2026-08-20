"""Document Registry: canonical paths, golden-rule CRM writes, WebDAV receipt."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes.documents.registry import DocumentRegistryError, register_document
from hermes_integrations.nextcloud_client import NextcloudClient, NextcloudError
from hermes_integrations.nextcloud_paths import (
    DocumentPathError,
    canonical_filename,
    canonical_folder,
    canonical_rel_path,
    pluralize_document_type,
    with_team_folder,
)
from hermes_integrations.zoho_document_registry import search_criteria, to_zoho_record
from hermes_integrations.zoho_document_registry_setup import (
    field_create_payload,
    load_field_rows,
    load_picklists,
    module_create_payload,
    plan_fields,
)


def test_pluralize_document_type_adds_s_without_doubling():
    assert pluralize_document_type("Declaration Page") == "Declaration Pages"
    assert pluralize_document_type("Declaration Pages") == "Declaration Pages"
    assert pluralize_document_type("Quote") == "Quotes"
    assert pluralize_document_type("Loss Run") == "Loss Runs"
    assert pluralize_document_type("Policy") == "Policies"


def test_canonical_path_matches_the_confirmed_tree():
    planned = canonical_rel_path(
        line_of_business="Commercial Lines",
        account="ABC Roofing",
        policy_type="General Liability",
        document_type="Declaration Page",
        renewal_cycle="2027",
        file_name="GL Dec Page.pdf",
        carrier="Travelers",
        base_path="",
    )
    assert planned["folder"] == (
        "Agency Documents/Commercial Lines/ABC Roofing/General Liability/"
        "Declaration Pages/2027"
    )
    assert planned["file_name"] == "Travelers GL Dec Page.pdf"
    assert planned["rel_path"].endswith("Travelers GL Dec Page.pdf")


def test_canonical_path_does_not_double_team_folder_when_base_path_set():
    folder = canonical_folder(
        line_of_business="personal",
        account="Smith Family",
        policy_type="Home",
        document_type="Application",
        renewal_cycle="2026",
        base_path="Agency Documents",
    )
    assert folder == "Personal Lines/Smith Family/Home/Applications/2026"
    assert not folder.startswith("Agency Documents/Agency Documents")


def test_canonical_filename_skips_carrier_if_already_prefixed():
    assert canonical_filename(file_name="Travelers GL Dec Page.pdf", carrier="Travelers") == (
        "Travelers GL Dec Page.pdf"
    )


def test_canonical_folder_requires_account():
    with pytest.raises(DocumentPathError, match="account_name"):
        canonical_folder(
            line_of_business="Commercial Lines",
            account="  ",
            policy_type="GL",
            document_type="Quote",
            renewal_cycle="2027",
        )


def test_with_team_folder_is_idempotent():
    assert with_team_folder("Agency Documents/Commercial Lines") == (
        "Agency Documents/Commercial Lines"
    )


def test_agency_account_dir_pluralizes_singular_type():
    nc = NextcloudClient(url="https://nc.example", user="hermes", app_password="pw")
    rel = nc.agency_account_dir(
        line="commercial",
        account="ABC Roofing",
        policy_type="General Liability",
        document_type="Declaration Page",
        year="2027",
    )
    assert rel.endswith("Declaration Pages/2027")


def _put_resp(code=201, file_id="00000259oczn5x60nrdu"):
    r = MagicMock()
    r.status_code = code
    r.text = ""
    r.headers = {"OC-FileId": file_id, "Content-Type": "application/pdf"}
    return r


def test_put_file_receipt_sends_automkcol_and_captures_file_id():
    sess = MagicMock()
    sess.put.return_value = _put_resp()
    nc = NextcloudClient(
        url="https://nc.example", user="hermes", app_password="pw", session=sess
    )
    receipt = nc.put_file_receipt(
        "Agency Documents/Commercial Lines/ABC Roofing/f.pdf",
        b"%PDF-1.4",
        content_type="application/pdf",
        auto_mkcol=True,
    )
    headers = sess.put.call_args.kwargs["headers"]
    assert headers["X-NC-WebDAV-AutoMkcol"] == "1"
    assert sess.request.call_count == 0  # no MKCOL on the happy path
    assert receipt["file_id"] == "00000259oczn5x60nrdu"
    assert "apps/files/?dir=/" in receipt["files_url"]
    assert receipt["webdav_url"].startswith("https://nc.example/remote.php/dav/files/")


def test_put_file_receipt_falls_back_to_mkcol_on_409():
    sess = MagicMock()
    sess.put.side_effect = [_put_resp(409, file_id=""), _put_resp()]
    sess.request.return_value = MagicMock(status_code=201, text="")
    nc = NextcloudClient(
        url="https://nc.example", user="hermes", app_password="pw", session=sess
    )
    receipt = nc.put_file_receipt(
        "Commercial Lines/ABC/GL/Quotes/2027/f.pdf",
        b"x",
        auto_mkcol=True,
    )
    assert sess.request.call_count >= 1
    assert receipt["file_id"] == "00000259oczn5x60nrdu"


def test_to_zoho_record_refuses_empty_url():
    with pytest.raises(Exception, match="Nextcloud_File_URL is empty"):
        to_zoho_record({"account_name": "ABC Roofing"}, {"file_name": "x.pdf"})


def test_to_zoho_record_sets_mandatory_url():
    record = to_zoho_record(
        {
            "account_name": "ABC Roofing",
            "document_type": "Declaration Page",
            "carrier": "Travelers",
            "policy_type": "General Liability",
            "renewal_cycle": "2027",
            "line_of_business": "Commercial Lines",
        },
        {
            "files_url": "https://nc.example/apps/files/?dir=/Agency Documents/x",
            "file_id": "00000259oczn5x60nrdu",
            "folder_path": "Agency Documents/Commercial Lines/ABC Roofing",
            "file_name": "Travelers GL Dec Page.pdf",
            "file_size": 12,
            "mime_type": "application/pdf",
        },
    )
    assert record["Nextcloud_File_URL"].startswith("https://nc.example/")
    assert record["Nextcloud_File_ID"] == "00000259oczn5x60nrdu"
    assert record["Account_Name"] == "ABC Roofing"


def test_search_criteria_joins_with_and():
    q = search_criteria(
        account_name="ABC Roofing",
        document_type="Declaration Page",
        carrier="Travelers",
    )
    assert q == (
        "(Account_Name:equals:ABC Roofing)and"
        "(Document_Type:equals:Declaration Page)and"
        "(Carrier:equals:Travelers)"
    )


def test_register_document_does_not_write_crm_when_put_fails():
    nc = MagicMock()
    nc.is_configured.return_value = True
    nc.base_path = ""
    nc.put_file_receipt.side_effect = NextcloudError("PUT failed: 500")
    zoho = MagicMock()
    with pytest.raises(DocumentRegistryError, match="Nextcloud PUT failed"):
        register_document(
            content=b"%PDF-1.4 demo",
            file_name="GL Dec Page.pdf",
            account_name="ABC Roofing",
            document_type="Declaration Page",
            policy_type="General Liability",
            line_of_business="Commercial Lines",
            renewal_cycle="2027",
            carrier="Travelers",
            write_to_zoho=True,
            nc=nc,
            zoho_upsert=zoho,
        )
    zoho.assert_not_called()


def test_register_document_refuses_crm_without_url_after_put():
    nc = MagicMock()
    nc.is_configured.return_value = True
    nc.base_path = ""
    nc.put_file_receipt.return_value = {
        "path": "Agency Documents/x.pdf",
        "files_url": "",
        "webdav_url": "",
        "file_id": None,
        "folder_path": "Agency Documents",
        "file_name": "x.pdf",
    }
    zoho = MagicMock()
    with pytest.raises(DocumentRegistryError, match="Nextcloud_File_URL is empty"):
        register_document(
            content=b"%PDF-1.4 demo",
            file_name="x.pdf",
            account_name="ABC Roofing",
            document_type="Quote",
            policy_type="General Liability",
            line_of_business="Commercial Lines",
            renewal_cycle="2027",
            write_to_zoho=True,
            nc=nc,
            zoho_upsert=zoho,
        )
    zoho.assert_not_called()


def test_register_document_uploads_then_upserts_zoho():
    nc = MagicMock()
    nc.is_configured.return_value = True
    nc.base_path = ""
    nc.put_file_receipt.return_value = {
        "path": "Agency Documents/Commercial Lines/ABC Roofing/General Liability/"
        "Declaration Pages/2027/Travelers GL Dec Page.pdf",
        "folder_path": "Agency Documents/Commercial Lines/ABC Roofing/"
        "General Liability/Declaration Pages/2027",
        "file_name": "Travelers GL Dec Page.pdf",
        "files_url": "https://nc.example/apps/files/?dir=/Agency Documents/...",
        "webdav_url": "https://nc.example/remote.php/dav/files/hermes/...",
        "file_id": "00000259oczn5x60nrdu",
        "file_size": 14,
        "mime_type": "application/pdf",
    }
    zoho = MagicMock(return_value={"id": "z-1", "action": "created"})
    out = register_document(
        content=b"%PDF-1.4 demo",
        file_name="GL Dec Page.pdf",
        account_name="ABC Roofing",
        document_type="Declaration Page",
        policy_type="General Liability",
        line_of_business="Commercial Lines",
        renewal_cycle="2027",
        carrier="Travelers",
        write_to_zoho=True,
        nc=nc,
        zoho_upsert=zoho,
    )
    assert out["ok"] is True
    assert out["crm"]["id"] == "z-1"
    zoho.assert_called_once()
    put_path = nc.put_file_receipt.call_args[0][0]
    assert put_path.startswith("Agency Documents/Commercial Lines/ABC Roofing/")
    assert put_path.endswith("Travelers GL Dec Page.pdf")
    assert nc.put_file_receipt.call_args.kwargs["auto_mkcol"] is True


def test_register_document_skips_zoho_when_flag_off():
    nc = MagicMock()
    nc.is_configured.return_value = True
    nc.base_path = ""
    nc.put_file_receipt.return_value = {
        "files_url": "https://nc.example/apps/files/?dir=/x",
        "webdav_url": "https://nc.example/remote.php/dav/files/hermes/x",
        "file_id": "abc",
        "folder_path": "Agency Documents/x",
        "file_name": "f.pdf",
        "file_size": 3,
        "mime_type": "application/pdf",
    }
    zoho = MagicMock()
    out = register_document(
        content=b"pdf",
        file_name="f.pdf",
        account_name="ABC Roofing",
        document_type="Quote",
        policy_type="GL",
        line_of_business="Commercial Lines",
        renewal_cycle="2027",
        write_to_zoho=False,
        nc=nc,
        zoho_upsert=zoho,
    )
    zoho.assert_not_called()
    assert out["crm"] is None
    assert "HERMES_WRITE_TO_ZOHO" in out["crm_skipped"]
    assert out["crm_payload"]["Nextcloud_File_URL"]


def test_module_create_payload_requires_a_profile():
    with pytest.raises(ValueError, match="profile"):
        module_create_payload([])
    body = module_create_payload(["111"])
    assert body["modules"][0]["api_name"] == "Document_Registry"
    assert body["modules"][0]["plural_label"] == "Document Registry"


def test_field_pack_marks_nextcloud_url_mandatory():
    rows = load_field_rows()
    url_row = next(r for r in rows if r["API_Name"] == "Nextcloud_File_URL")
    assert url_row["Mandatory"] == "Y"
    assert url_row["Data_Type"] == "URL"
    picklists = load_picklists()
    plan = plan_fields(
        existing_api_names=set(),
        existing_modules={"Accounts"},
        picklists=picklists,
        rows=rows,
    )
    creates = [s for s in plan if s["action"] == "create_field"]
    names = {s["api_name"] for s in creates}
    assert "Nextcloud_File_URL" in names
    assert "Name" not in names  # display field on the module
    policy = next(s for s in plan if s["api_name"] == "Policy")
    assert policy["action"] == "skip"


def test_url_field_payload_is_website_type():
    row = {
        "API_Name": "Nextcloud_File_URL",
        "Display_Label": "Nextcloud File URL",
        "Data_Type": "URL",
        "Length": "450",
        "Picklist_Source": "",
    }
    payload = field_create_payload(row)
    assert payload["fields"][0]["data_type"] == "website"


@patch("hermes.documents.registry.register_document")
def test_upload_endpoint_returns_registry_result(mock_register):
    from fastapi.testclient import TestClient

    from hermes.api import app

    mock_register.return_value = {"ok": True, "crm": None, "nextcloud": {"file_id": "1"}}
    import base64

    resp = TestClient(app).post(
        "/api/document-registry/upload",
        json={
            "account_name": "ABC Roofing",
            "document_type": "Declaration Page",
            "policy_type": "General Liability",
            "line_of_business": "Commercial Lines",
            "renewal_cycle": "2027",
            "file_name": "GL Dec Page.pdf",
            "content_base64": base64.b64encode(b"%PDF-1.4").decode(),
            "write_to_zoho": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_register.assert_called_once()
