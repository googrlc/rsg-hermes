"""Document Registry: Clients/{name} paths, Lead XOR Account, golden-rule CRM writes."""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from hermes.documents.registry import (
    DocumentRegistryError,
    register_document,
    resolve_party,
)
from hermes_integrations.nextcloud_client import NextcloudClient, NextcloudError
from hermes_integrations.nextcloud_paths import (
    DocumentPathError,
    canonical_filename,
    canonical_folder,
    canonical_rel_path,
    category_for_document_type,
)
from hermes_integrations.zoho_document_registry import search_criteria, to_zoho_record
from hermes_integrations.zoho_document_registry_setup import (
    RELATED_LIST_LABEL,
    field_create_payload,
    load_field_rows,
    load_picklists,
    module_create_payload,
    plan_fields,
)


def test_category_maps_document_type_to_clients_subfolder():
    assert category_for_document_type("Intake") == "Intake"
    assert category_for_document_type("Quote") == "Quotes"
    assert category_for_document_type("Policy") == "Policies"
    assert category_for_document_type("Declaration Page") == "Policies"
    assert category_for_document_type("Certificate of Insurance") == "COIs"
    assert category_for_document_type("Renewal Review") == "Renewal Reviews"


def test_canonical_path_is_clients_tree_not_commercial_lines():
    planned = canonical_rel_path(
        party_name="Jane Lead",
        document_type="Intake",
        file_name="app.pdf",
        carrier="Progressive",
    )
    assert planned["folder"] == "Clients/Jane Lead/Intake"
    assert planned["client_root"] == "Clients/Jane Lead"
    assert planned["file_name"] == "Progressive app.pdf"
    assert "Commercial Lines" not in planned["rel_path"]
    assert "Agency Documents" not in planned["rel_path"]


def test_canonical_folder_requires_party_name():
    with pytest.raises(DocumentPathError, match="display name"):
        canonical_folder(party_name="  ", document_type="Quote")


def test_canonical_filename_skips_carrier_if_already_prefixed():
    assert canonical_filename(file_name="Travelers GL Dec Page.pdf", carrier="Travelers") == (
        "Travelers GL Dec Page.pdf"
    )


def test_resolve_party_lead_xor_account():
    lead = resolve_party(lead_id="L1", lead_name="Jane Lead")
    assert lead["kind"] == "lead"
    assert lead["party_name"] == "Jane Lead"
    assert lead["account_id"] == ""
    acct = resolve_party(account_id="A1", account_name="ABC Roofing")
    assert acct["kind"] == "account"
    assert acct["party_name"] == "ABC Roofing"
    with pytest.raises(DocumentRegistryError, match="exactly one"):
        resolve_party(lead_name="Jane", account_name="ABC")
    with pytest.raises(DocumentRegistryError, match="party is required"):
        resolve_party()
    with pytest.raises(DocumentRegistryError, match="lead_name is required"):
        resolve_party(lead_id="L1")


def _nc_mock(*, existed: bool, receipt: dict | None = None) -> MagicMock:
    nc = MagicMock()
    nc.is_configured.return_value = True
    nc.base_path = ""
    nc.path_exists.return_value = existed
    nc._rel_with_base.side_effect = lambda p: p
    nc.put_file_receipt.return_value = receipt or {
        "path": "Clients/Jane Lead/Intake/app.pdf",
        "folder_path": "Clients/Jane Lead/Intake",
        "file_name": "app.pdf",
        "files_url": "https://nc.example/f/15700",
        "webdav_url": "https://nc.example/remote.php/dav/files/hermes/Clients/Jane Lead/Intake/app.pdf",
        "file_id": "15700",
        "file_size": 8,
        "mime_type": "application/pdf",
    }
    return nc


def test_lead_only_upload_creates_clients_folder_and_lead_lookup():
    nc = _nc_mock(existed=False)
    zoho = MagicMock(return_value={"id": "z-1", "action": "created"})
    out = register_document(
        content=b"%PDF-1.4",
        file_name="app.pdf",
        document_type="Intake",
        policy_type="Home",
        line_of_business="Personal Lines",
        renewal_cycle="2026",
        lead_id="7529682000000999001",
        lead_name="Jane Lead",
        account_name="",
        write_to_zoho=True,
        nc=nc,
        zoho_upsert=zoho,
    )
    assert out["ok"] is True
    assert out["path"]["client_root"] == "Clients/Jane Lead"
    assert out["path"]["folder"] == "Clients/Jane Lead/Intake"
    nc.ensure_client_folders.assert_called_once_with("Jane Lead")
    put_path = nc.put_file_receipt.call_args[0][0]
    assert put_path.startswith("Clients/Jane Lead/Intake/")
    meta = zoho.call_args[0][0]
    receipt = zoho.call_args[0][1]
    record = to_zoho_record(meta, receipt)
    assert record["Lead"] == {"id": "7529682000000999001"}
    assert "Account" not in record
    assert record["Account_Name"] == "Jane Lead"
    assert record["Nextcloud_File_URL"] == "https://nc.example/f/15700"


def test_account_upload_still_works():
    nc = _nc_mock(
        existed=True,
        receipt={
            "path": "Clients/ABC Roofing/Policies/dec.pdf",
            "folder_path": "Clients/ABC Roofing/Policies",
            "file_name": "Travelers dec.pdf",
            "files_url": "https://nc.example/f/15701",
            "webdav_url": "https://nc.example/remote.php/dav/files/hermes/x",
            "file_id": "15701",
            "file_size": 12,
            "mime_type": "application/pdf",
        },
    )
    zoho = MagicMock(return_value={"id": "z-2", "action": "created"})
    out = register_document(
        content=b"%PDF-1.4 demo",
        file_name="dec.pdf",
        document_type="Declaration Page",
        policy_type="General Liability",
        line_of_business="Commercial Lines",
        renewal_cycle="2027",
        account_id="acct-1",
        account_name="ABC Roofing",
        carrier="Travelers",
        write_to_zoho=True,
        nc=nc,
        zoho_upsert=zoho,
    )
    assert out["ok"] is True
    assert out["path"]["folder"] == "Clients/ABC Roofing/Policies"
    nc.ensure_client_folders.assert_not_called()
    nc.ensure_dirs.assert_called_once()
    record = to_zoho_record(zoho.call_args[0][0], zoho.call_args[0][1])
    assert record["Account"] == {"id": "acct-1"}
    assert "Lead" not in record


def test_missing_folder_is_created_existing_folder_is_reused():
    missing = _nc_mock(existed=False)
    out_missing = register_document(
        content=b"pdf",
        file_name="f.pdf",
        document_type="Quote",
        policy_type="Home",
        line_of_business="Personal Lines",
        renewal_cycle="2026",
        lead_name="Pat Prospect",
        write_to_zoho=False,
        nc=missing,
        zoho_upsert=MagicMock(),
    )
    missing.ensure_client_folders.assert_called_once_with("Pat Prospect")
    assert out_missing["folder"]["folder_created"] is True
    assert out_missing["folder"]["folder_existed"] is False

    existing = _nc_mock(existed=True)
    out = register_document(
        content=b"pdf",
        file_name="f.pdf",
        document_type="Quote",
        policy_type="Home",
        line_of_business="Personal Lines",
        renewal_cycle="2026",
        lead_name="Pat Prospect",
        write_to_zoho=False,
        nc=existing,
        zoho_upsert=MagicMock(),
    )
    existing.ensure_client_folders.assert_not_called()
    assert out["folder"]["folder_existed"] is True
    assert out["folder"]["folder_created"] is False


def test_register_document_does_not_write_crm_when_put_fails():
    nc = _nc_mock(existed=True)
    nc.put_file_receipt.side_effect = NextcloudError("PUT failed: 500")
    zoho = MagicMock()
    with pytest.raises(DocumentRegistryError, match="Nextcloud PUT failed"):
        register_document(
            content=b"%PDF-1.4 demo",
            file_name="x.pdf",
            document_type="Quote",
            policy_type="GL",
            line_of_business="Commercial Lines",
            renewal_cycle="2027",
            account_name="ABC Roofing",
            write_to_zoho=True,
            nc=nc,
            zoho_upsert=zoho,
        )
    zoho.assert_not_called()


def test_register_document_refuses_crm_without_url_after_put():
    nc = _nc_mock(
        existed=True,
        receipt={
            "path": "Clients/ABC Roofing/Quotes/x.pdf",
            "files_url": "",
            "webdav_url": "https://nc.example/remote.php/dav/files/hermes/x",
            "file_id": None,
            "folder_path": "Clients/ABC Roofing/Quotes",
            "file_name": "x.pdf",
        },
    )
    zoho = MagicMock()
    with pytest.raises(DocumentRegistryError, match="Nextcloud_File_URL is empty"):
        register_document(
            content=b"%PDF-1.4 demo",
            file_name="x.pdf",
            document_type="Quote",
            policy_type="GL",
            line_of_business="Commercial Lines",
            renewal_cycle="2027",
            account_name="ABC Roofing",
            write_to_zoho=True,
            nc=nc,
            zoho_upsert=zoho,
        )
    zoho.assert_not_called()


def test_to_zoho_record_refuses_empty_url():
    with pytest.raises(Exception, match="Nextcloud_File_URL is empty"):
        to_zoho_record({"account_name": "ABC Roofing"}, {"file_name": "x.pdf"})


def test_search_criteria_uses_account_name_for_lead_or_account():
    q = search_criteria(lead_name="Jane Lead", document_type="Intake")
    assert q == "(Account_Name:equals:Jane Lead)and(Document_Type:equals:Intake)"


def test_module_create_payload_requires_a_profile():
    with pytest.raises(ValueError, match="profile"):
        module_create_payload([])
    body = module_create_payload(["111"])
    assert body["modules"][0]["api_name"] == "Document_Registry"
    assert body["modules"][0]["plural_label"] == "Document Registry"


def test_field_pack_lead_lookup_account_optional_url_mandatory():
    rows = load_field_rows()
    by_api = {r["API_Name"]: r for r in rows}
    assert by_api["Lead"]["Data_Type"] == "Lookup (Leads)"
    assert by_api["Account"]["Mandatory"] == "N"
    assert by_api["Account_Name"]["Mandatory"] == "N"
    assert by_api["Nextcloud_File_URL"]["Mandatory"] == "Y"
    picklists = load_picklists()
    plan = plan_fields(
        existing_api_names=set(),
        existing_modules={"Accounts", "Leads", "Deals"},
        picklists=picklists,
        rows=rows,
    )
    creates = {s["api_name"]: s for s in plan if s["action"] == "create_field"}
    skips = {s["api_name"]: s for s in plan if s["action"] == "skip"}
    assert "Lead" in creates
    assert "Account" in creates
    assert "Deal" in creates
    assert skips["Policy"]["reason"].startswith("Policies")
    assert skips["Renewal"]["reason"].startswith("Renewals")
    lead_payload = field_create_payload(by_api["Lead"])
    assert lead_payload["fields"][0]["lookup"]["display_label"] == RELATED_LIST_LABEL
    assert RELATED_LIST_LABEL == "Nextcloud Files"


def test_integer_field_length_capped_at_zoho_max():
    row = {
        "API_Name": "File_Size",
        "Display_Label": "File Size",
        "Data_Type": "Number",
        "Length": "18",
        "Picklist_Source": "",
    }
    payload = field_create_payload(row)
    assert payload["fields"][0]["data_type"] == "integer"
    assert payload["fields"][0]["length"] == 9


def _put_resp(code=201, file_id="00000259oczn5x60nrdu"):
    r = MagicMock()
    r.status_code = code
    r.text = ""
    r.headers = {"OC-FileId": file_id, "Content-Type": "application/pdf"}
    r.content = b""
    return r


def _propfind_fileid(file_id="15700"):
    r = MagicMock()
    r.status_code = 207
    r.text = ""
    r.content = (
        b'<?xml version="1.0"?>'
        b'<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        b"<d:response><d:propstat><d:prop>"
        b"<oc:fileid>" + file_id.encode() + b"</oc:fileid>"
        b"</d:prop></d:propstat></d:response></d:multistatus>"
    )
    return r


def test_put_file_receipt_sends_automkcol_and_stamps_permalink():
    sess = MagicMock()
    sess.put.return_value = _put_resp()
    sess.request.return_value = _propfind_fileid()
    nc = NextcloudClient(
        url="https://nc.example", user="hermes", app_password="pw", session=sess
    )
    receipt = nc.put_file_receipt(
        "Clients/Jane Lead/Intake/app.pdf",
        b"%PDF-1.4",
        content_type="application/pdf",
        auto_mkcol=True,
    )
    headers = sess.put.call_args.kwargs["headers"]
    assert headers["X-NC-WebDAV-AutoMkcol"] == "1"
    assert receipt["file_id"] == "15700"
    assert receipt["files_url"] == "https://nc.example/f/15700"


def test_put_file_receipt_falls_back_to_mkcol_on_409():
    sess = MagicMock()
    sess.put.side_effect = [_put_resp(409, file_id=""), _put_resp()]

    def _request(method, url, **kw):
        if method == "PROPFIND":
            return _propfind_fileid()
        return MagicMock(status_code=201, text="", content=b"")

    sess.request.side_effect = _request
    nc = NextcloudClient(
        url="https://nc.example", user="hermes", app_password="pw", session=sess
    )
    receipt = nc.put_file_receipt(
        "Clients/ABC Roofing/Quotes/f.pdf",
        b"x",
        auto_mkcol=True,
    )
    assert sess.request.call_count >= 1
    assert receipt["files_url"] == "https://nc.example/f/15700"


@patch("hermes.documents.registry.register_document")
def test_upload_endpoint_accepts_lead_without_account_name(mock_register):
    from fastapi.testclient import TestClient

    from hermes.api import app

    mock_register.return_value = {"ok": True, "crm": None, "nextcloud": {"file_id": "15700"}}
    resp = TestClient(app).post(
        "/api/document-registry/upload",
        json={
            "lead_name": "Jane Lead",
            "lead_id": "L1",
            "account_name": "",
            "document_type": "Intake",
            "policy_type": "Home",
            "line_of_business": "Personal Lines",
            "renewal_cycle": "2026",
            "file_name": "app.pdf",
            "content_base64": base64.b64encode(b"%PDF-1.4").decode(),
            "write_to_zoho": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    kwargs = mock_register.call_args.kwargs
    assert kwargs["lead_name"] == "Jane Lead"
    assert kwargs["account_name"] == ""


@patch("hermes.documents.registry.register_document")
def test_upload_endpoint_account_still_works(mock_register):
    from fastapi.testclient import TestClient

    from hermes.api import app

    mock_register.return_value = {"ok": True, "crm": {"id": "z-1"}}
    resp = TestClient(app).post(
        "/api/document-registry/upload",
        json={
            "account_name": "ABC Roofing",
            "account_id": "A1",
            "document_type": "Policy",
            "policy_type": "General Liability",
            "line_of_business": "Commercial Lines",
            "renewal_cycle": "2027",
            "file_name": "dec.pdf",
            "content_base64": base64.b64encode(b"%PDF-1.4").decode(),
            "write_to_zoho": True,
        },
    )
    assert resp.status_code == 200
    kwargs = mock_register.call_args.kwargs
    assert kwargs["account_name"] == "ABC Roofing"
    assert kwargs["lead_name"] == ""


def test_upload_page_is_served():
    from fastapi.testclient import TestClient

    from hermes.api import app

    resp = TestClient(app).get("/command-center/document-registry")
    assert resp.status_code == 200
    assert "Document Registry" in resp.text
    assert "/api/document-registry/upload" in resp.text


def test_file_zoho_attachment_files_then_deletes_temp_attachment():
    from hermes.documents.registry import file_zoho_attachment

    zoho = MagicMock()
    zoho.get_record.return_value = {
        "id": "reg-1",
        "Name": "app.pdf",
        "Document_Type": "Intake",
        "Policy_Type": "Home",
        "Line_of_Business": "Personal Lines",
        "Renewal_Cycle": "2026",
        "Account_Name": "Jane Lead",
        "Lead": {"id": "L1", "name": "Jane Lead"},
        "Nextcloud_File_URL": "",
    }
    zoho.list_attachments.return_value = [{"id": "att-1", "File_Name": "app.pdf"}]
    zoho.download_attachment.return_value = b"%PDF-1.4"
    nc = _nc_mock(existed=False)
    upsert = MagicMock(return_value={"id": "reg-1", "action": "updated"})
    out = file_zoho_attachment(
        "reg-1", write_to_zoho=True, nc=nc, zoho=zoho, zoho_upsert=upsert
    )
    assert out["ok"] is True
    assert out["attachment_deleted"] == "att-1"
    zoho.delete_attachment.assert_called_once_with("Document_Registry", "reg-1", "att-1")
    meta = upsert.call_args[0][0]
    assert meta["lead_id"] == "L1"
    assert meta["account_id"] == ""

