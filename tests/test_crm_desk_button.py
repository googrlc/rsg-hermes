"""CRM Account → Desk Create Service Request button."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from hermes.desk.crm_button import (
    BUTTON_NAME,
    CONNECTION_NAME,
    DEFAULT_CATEGORY,
    MODULE,
    POSITIONS,
    build_ticket_payload,
    crm_account_identity,
    custom_link_url,
    render_deluge,
)
from hermes.desk.live import DEPARTMENT_ID, LAYOUT_IDS, ORG_ID, TEAM_IDS
from hermes.desk.service_request import create_from_crm_account
from hermes.desk.spec import CUSTOM_FUNCTIONS
from hermes.desk.titles import case_title

DOCS = Path("docs/zoho-desk")


def test_button_is_short_enough_for_crm():
    assert len(BUTTON_NAME) <= 30
    assert MODULE == "Accounts"
    assert "View Page" in POSITIONS
    assert any("each record" in item.lower() for item in POSITIONS)


def test_payload_stamps_crm_account_and_does_not_touch_ams():
    payload = build_ticket_payload(
        {
            "id": "7529682000001111111",
            "Account_Name": "ABC Trucking LLC",
            "Email": "ops@abctrucking.test",
        }
    )
    assert payload["departmentId"] == DEPARTMENT_ID
    assert payload["layoutId"] == LAYOUT_IDS["General Service"]
    assert payload["teamId"] == TEAM_IDS["Service Intake"]
    assert payload["channel"] == "CRM"
    assert payload["classification"] == DEFAULT_CATEGORY
    assert payload["priority"] == "Normal"
    assert payload["cf"]["cf_crm_account_id"] == "7529682000001111111"
    assert payload["contact"]["email"] == "ops@abctrucking.test"
    assert payload["subject"] == case_title(
        "General Service", "ABC Trucking LLC", None, "Service request"
    )
    assert "Momentum" in payload["description"]
    blob = str(payload)
    assert "nowcerts" not in blob.lower()
    assert "ams_write" not in blob.lower()


def test_payload_uses_contact_id_when_resolved():
    payload = build_ticket_payload(
        {"id": "1", "Account_Name": "Patel Household"},
        contact_id="1435573000000999001",
        desk_account_id="1435573000000888001",
        policy_number="HO-99",
        short_request="Policy copy",
    )
    assert payload["contactId"] == "1435573000000999001"
    assert payload["accountId"] == "1435573000000888001"
    assert "contact" not in payload
    assert payload["subject"] == "Service | Patel Household | HO-99 | Policy copy"


def test_deluge_targets_live_desk_and_crm_account_id():
    script = render_deluge()
    assert CONNECTION_NAME in script
    assert ORG_ID in script
    assert DEPARTMENT_ID in script
    assert LAYOUT_IDS["General Service"] in script
    assert TEAM_IDS["Service Intake"] in script
    assert "cf_crm_account_id" in script
    assert "openUrl" in script
    assert "zoho.desk.create" in script
    assert "Momentum" in script


def test_identity_falls_back_to_name_keys():
    ident = crm_account_identity({"name": "JB Noble", "email": "jb@example.test"})
    assert ident["account_name"] == "JB Noble"
    assert ident["email"] == "jb@example.test"
    assert ident["crm_account_id"] == ""


def test_custom_link_stays_on_the_rsg_portal():
    url = custom_link_url()
    assert "rsg10761" in url
    assert "${Accounts.Id}" in url
    assert "${Accounts.Account_Name}" in url


def test_python_create_reuses_desk_contact_and_opens_ticket():
    desk = MagicMock()
    desk.search_accounts.return_value = [{"id": "acc-1"}]
    desk.search_contacts.return_value = [{"id": "con-1"}]
    desk.create_ticket.return_value = {
        "id": "tix-1",
        "webUrl": "https://desk.zoho.com/support/rsg10761/ShowHomePage.do#Cases/dv/tix-1",
    }
    ticket = create_from_crm_account(
        desk,
        {"id": "crm-9", "Account_Name": "3D Pumps", "Email": "office@3dpumps.test"},
    )
    desk.create_account.assert_not_called()
    desk.create_contact.assert_not_called()
    posted = desk.create_ticket.call_args.args[0]
    assert posted["contactId"] == "con-1"
    assert posted["accountId"] == "acc-1"
    assert posted["cf"]["cf_crm_account_id"] == "crm-9"
    assert ticket["id"] == "tix-1"


def test_docs_and_catalog_include_the_account_button():
    assert "CF-07 CRM Account Create Service Request button" in CUSTOM_FUNCTIONS
    doc = (DOCS / "crm_account_button.md").read_text(encoding="utf-8")
    assert BUTTON_NAME in doc
    assert "View Page" in doc
    assert "List View" in doc
    assert "zohodesk" in doc
    assert ORG_ID in doc or "render_deluge" in doc
    functions = (DOCS / "custom_functions.md").read_text(encoding="utf-8")
    assert "CF-07" in functions
    checklist = (DOCS / "SETUP_CHECKLIST.md").read_text(encoding="utf-8")
    assert "Create Service Request" in checklist
