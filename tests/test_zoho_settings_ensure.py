"""Settings-API helpers: CSRF/origin parsing and ensure loop against a fake client."""

from __future__ import annotations

import pytest

from hermes_integrations.zoho_document_fields import DOCUMENT_URL_FIELDS
from hermes_integrations.zoho_settings_ensure import (
    crm_csrf_from_cookie_header,
    crm_origin_from_url,
    decode_settings_response,
    list_module_api_names,
    process_module,
    settings_url,
)


def test_crm_origin_from_us_and_eu_hosts():
    assert crm_origin_from_url("https://crm.zoho.com/crm/settings/modules") == "https://crm.zoho.com"
    assert crm_origin_from_url("https://crm.zoho.eu/crm/tab/Accounts") == "https://crm.zoho.eu"
    assert crm_origin_from_url(
        "https://crmplus.zoho.com/rsg10761/index.do/cxapp/crm/org935119573/settings/modules"
    ) == "https://crmplus.zoho.com"


def test_crm_org_from_url():
    from hermes_integrations.zoho_settings_ensure import crm_org_from_url

    assert crm_org_from_url(
        "https://crmplus.zoho.com/rsg10761/index.do/cxapp/crm/org935119573/tab/Accounts/1"
    ) == "935119573"


def test_crm_origin_rejects_signin():
    with pytest.raises(RuntimeError, match="Not on a Zoho CRM host"):
        crm_origin_from_url("https://accounts.zoho.com/signin?servicename=ZohoCRM")


def test_csrf_prefers_crmcsr():
    header = "foo=bar; crmcsr=abc%2Fdef; session=1"
    assert crm_csrf_from_cookie_header(header) == "abc/def"


def test_settings_url_includes_module_query():
    url = settings_url("https://crm.zoho.com", "/settings/fields", {"module": "Accounts"})
    assert url == "https://crm.zoho.com/crm/v8/settings/fields?module=Accounts"


def test_decode_rejects_html_login_page():
    with pytest.raises(RuntimeError, match="HTML"):
        decode_settings_response("GET", "https://crm.zoho.com/crm/v8/settings/modules", 200, "<html>sign in</html>")


def test_decode_rejects_http_error():
    with pytest.raises(RuntimeError, match="HTTP 401"):
        decode_settings_response("GET", "https://example/x", 401, '{"code":"INVALID"}')


class FakeSettingsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None, dict | None]] = []
        self.fields_by_module: dict[str, list[dict]] = {
            "Accounts": [],
            "Policies": [
                {"id": "p1", "api_name": "Primary_Folder_URL", "field_label": "Primary Folder URL"},
                {"id": "p2", "api_name": "Document_URL", "field_label": "Document URL"},
            ],
        }
        self.layouts = {
            "Accounts": {
                "layouts": [{"id": "lay1", "name": "Standard", "sections": []}]
            },
            "Policies": {
                "layouts": [
                    {
                        "id": "lay2",
                        "name": "Standard",
                        "sections": [
                            {
                                "id": "sec2",
                                "display_label": "Documents",
                                "fields": [
                                    {"id": "p1", "type": "used"},
                                    {"id": "p2", "type": "used"},
                                ],
                            }
                        ],
                    }
                ]
            },
        }

    def request(self, method, path, *, query=None, body=None):
        self.calls.append((method, path, query, body))
        if path == "/settings/modules":
            return {"modules": [{"api_name": "Accounts"}, {"api_name": "Policies"}, {"api_name": "Deals"}]}
        if path == "/settings/fields" and method == "GET":
            module = (query or {}).get("module", "")
            return {"fields": list(self.fields_by_module.get(module, []))}
        if path == "/settings/fields" and method == "POST":
            module = (query or {}).get("module", "")
            label = body["fields"][0]["field_label"]
            new = {"id": f"new-{label}", "api_name": label.replace(" ", "_"), "field_label": label}
            self.fields_by_module.setdefault(module, []).append(new)
            return {"fields": [new]}
        if path == "/settings/layouts":
            module = (query or {}).get("module", "")
            return self.layouts.get(module, {"layouts": []})
        if path.startswith("/settings/layouts/") and method == "PATCH":
            return {"layouts": [{"id": path.rsplit("/", 1)[-1]}]}
        raise AssertionError(f"unexpected {method} {path}")


def test_list_module_api_names():
    client = FakeSettingsClient()
    assert list_module_api_names(client) == {"Accounts", "Policies", "Deals"}


def test_process_module_posts_missing_account_url_and_patches_layout(capsys):
    client = FakeSettingsClient()
    process_module(client, "Accounts", apply=True, present={"Accounts", "Policies"})
    methods = [(m, p) for m, p, _, _ in client.calls]
    assert ("POST", "/settings/fields") in methods
    assert any(p.startswith("/settings/layouts/") and m == "PATCH" for m, p in methods)
    posted = [c for c in client.calls if c[0] == "POST"][0]
    assert posted[2] == {"module": "Accounts"}
    assert posted[3]["fields"][0]["data_type"] == "website"
    assert posted[3]["fields"][0]["field_label"] == "Nextcloud Folder URL"
    out = capsys.readouterr().out
    assert "create field: Nextcloud Folder URL" in out
    assert "create field: Nextcloud Folder Link" in out
    posts = [c for c in client.calls if c[0] == "POST"]
    assert [c[3]["fields"][0]["data_type"] for c in posts] == ["website", "text", "text"]


def test_process_module_creates_text_link_when_website_exists(capsys):
    client = FakeSettingsClient()
    client.fields_by_module["Accounts"] = [
        {"id": "old", "api_name": "Nextcloud_Folder_URL", "field_label": "Nextcloud Folder URL"}
    ]
    process_module(client, "Accounts", apply=True, present={"Accounts"})
    posts = [c for c in client.calls if c[0] == "POST"]
    assert [c[3]["fields"][0]["field_label"] for c in posts] == [
        "Nextcloud Folder Link",
        "Nextcloud File ID",
    ]
    assert all(c[3]["fields"][0]["data_type"] == "text" for c in posts)
    assert "create field: Nextcloud Folder Link" in capsys.readouterr().out


def test_process_module_skips_claims_when_absent(capsys):
    client = FakeSettingsClient()
    process_module(client, "Claims", apply=True, present={"Accounts"})
    assert client.calls == []
    assert "module not in this Zoho org" in capsys.readouterr().out


def test_process_module_dry_run_does_not_post():
    client = FakeSettingsClient()
    process_module(client, "Accounts", apply=False, present={"Accounts"})
    assert not any(c[0] == "POST" for c in client.calls)


def test_process_module_policies_already_on_layout(capsys):
    client = FakeSettingsClient()
    process_module(client, "Policies", apply=True, present={"Policies"})
    assert not any(c[0] == "POST" for c in client.calls)
    assert not any(c[0] == "PATCH" for c in client.calls)
    assert "document fields already exist" in capsys.readouterr().out
    assert DOCUMENT_URL_FIELDS["Policies"]
