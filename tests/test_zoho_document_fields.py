"""Zoho document-link field spec: missing-field detection and URL guards."""

from __future__ import annotations

from hermes_integrations.zoho_document_fields import (
    DOCUMENT_URL_FIELDS,
    field_create_payload,
    is_http_url,
    missing_document_fields,
    normalize_api_name,
    website_create_payload,
)


def test_normalize_strips_zoho_suffixes():
    assert normalize_api_name("Nextcloud_Folder_URL__s") == "Nextcloud_Folder_URL"
    assert normalize_api_name("Document_URL__c") == "Document_URL"
    assert normalize_api_name("Primary_Folder_URL") == "Primary_Folder_URL"


def test_missing_when_module_empty():
    missing = missing_document_fields("Accounts", [])
    assert [s["api_name"] for s in missing] == [
        "Nextcloud_Folder_URL",
        "Nextcloud_Folder_Link",
        "Nextcloud_File_ID",
    ]


def test_existing_website_field_still_needs_text_link():
    fields = [{"api_name": "Nextcloud_Folder_URL__s", "id": "1", "field_label": "Nextcloud Folder URL"}]
    assert [s["api_name"] for s in missing_document_fields("Accounts", fields)] == [
        "Nextcloud_Folder_Link",
        "Nextcloud_File_ID",
    ]


def test_existing_label_counts_as_present():
    fields = [{"api_name": "Custom_19", "id": "2", "field_label": "Document URL"}]
    missing = missing_document_fields("Policies", fields)
    assert [s["api_name"] for s in missing] == ["Primary_Folder_URL"]


def test_website_payload_is_url_field():
    spec = DOCUMENT_URL_FIELDS["Policies"][1]
    payload = website_create_payload(spec)
    assert payload["data_type"] == "website"
    assert payload["field_label"] == "Document URL"
    assert payload["length"] == 450
    assert len(payload["tooltip"]["value"]) <= 32


def test_text_link_payload_is_single_line():
    spec = DOCUMENT_URL_FIELDS["Accounts"][1]
    payload = field_create_payload(spec)
    assert spec["api_name"] == "Nextcloud_Folder_Link"
    assert payload["data_type"] == "text"
    assert payload["length"] == 255
    assert len(payload["tooltip"]["value"]) <= 32


def test_is_http_url_rejects_relative_paths():
    assert is_http_url("https://nextcloud.example/apps/files/?dir=/Clients/Acme")
    assert not is_http_url("Clients/Acme Plumbing LLC")
    assert not is_http_url("")
    assert not is_http_url(None)


def test_claims_and_certificates_are_in_the_pack():
    assert "Claims" in DOCUMENT_URL_FIELDS
    assert "Certificates" in DOCUMENT_URL_FIELDS
    assert {s["api_name"] for s in DOCUMENT_URL_FIELDS["Claims"]} == {
        "Primary_Folder_URL",
        "Document_URL",
    }


def test_filed_documents_map_to_nextcloud_subfolders():
    from hermes_integrations.zoho_document_fields import (
        FILED_DOCUMENT_TYPES,
        FILED_DOCUMENTS_MODULE,
        FILED_DOCUMENTS_RELATED_LIST,
        PARENT_FOLDER_BY_TYPE,
        PARENT_TYPE_BY_MODULE,
    )

    assert FILED_DOCUMENTS_MODULE == "Filed_Documents"
    assert FILED_DOCUMENTS_RELATED_LIST == "Nextcloud Files"
    assert set(FILED_DOCUMENT_TYPES) == set(PARENT_FOLDER_BY_TYPE)
    assert PARENT_TYPE_BY_MODULE["Deals"] == "Quote"
    assert PARENT_FOLDER_BY_TYPE["Quote"] == "Quotes"
    assert PARENT_FOLDER_BY_TYPE["Policy"] == "Policies"
