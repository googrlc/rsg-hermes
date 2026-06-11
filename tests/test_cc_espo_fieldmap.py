"""The Espo write contract — correct casing + strict enum coercion."""
import base64

from hermes.command_center.espo_fieldmap import (
    account_write_payload,
    basic_auth_header,
    map_account_type,
    map_business_entity,
)


def test_business_entity_maps_and_rejects_the_bug_value():
    assert map_business_entity("llc") == "LLC"
    assert map_business_entity("LLC") == "LLC"
    assert map_business_entity("s_corp") == "S-Corp"
    assert map_business_entity("not_for_profit") == "Non-Profit"
    assert map_business_entity("individual") is None     # person, not a business
    assert map_business_entity("Personal") is None        # the value that 400'd the sync
    assert map_business_entity("") is None


def test_account_type_maps():
    assert map_account_type("Commercial") == "Commercial Lines"
    assert map_account_type("Personal Lines") == "Personal Lines"
    assert map_account_type("benefits") == "Group Benefits"
    assert map_account_type("bogus") is None


def test_write_payload_casing_enum_and_omitting_none():
    body = account_write_payload({
        "name": "Acme Plumbing", "email": "a@b.com",
        "city": "Atlanta", "state": "GA", "phone": None,
        "entity_type": "llc", "client_type": "Commercial",
        "xdate": "2026-07-01", "fein": "12-3456789",
    })
    assert body["name"] == "Acme Plumbing"
    assert body["emailAddress"] == "a@b.com"            # core camelCase
    assert body["billingAddressCity"] == "Atlanta"
    assert body["businessEntity"] == "LLC"             # mapped enum
    assert body["account_type"] == "Commercial Lines"  # custom snake_case
    assert body["x_date"] == "2026-07-01"              # XDATE custom field
    assert body["fein"] == "12-3456789"
    assert "phoneNumber" not in body                    # None never sent


def test_invalid_business_entity_falls_back_to_entity_type():
    body = account_write_payload({
        "name": "X", "business_entity": "Personal", "entity_type": "corporation",
    })
    assert body["businessEntity"] == "Corporation"


def test_basic_auth_header_is_key_colon():
    h = basic_auth_header("abc123")
    assert h["Authorization"].startswith("Basic ")
    assert base64.b64decode(h["Authorization"].split()[1]).decode() == "abc123:"
