"""Tests for hermes/intake/nowcerts_map.py — intake account -> NowCerts Insured.

Uses the connector contract: insuredType '0'/'1', type 0/1; PascalCase name/address.
"""

from __future__ import annotations

from hermes.intake import nowcerts_map as m


def test_normalize_insured_type():
    assert m.normalize_insured_type("commercial lines") == "Commercial"
    assert m.normalize_insured_type("Home") == "Personal"
    assert m.normalize_insured_type("") is None


def test_insured_type_code():
    assert m.insured_type_code("commercial") == "0"
    assert m.insured_type_code("personal") == "1"
    assert m.insured_type_code("") is None


def test_normalize_prospect_type():
    assert m.normalize_prospect_type("hot") == "Hot_Prospect"
    assert m.normalize_prospect_type("cold") == "Cold_Prospect"
    assert m.normalize_prospect_type(None) == "Prospect"


def test_map_commercial_uses_codes_and_commercial_name():
    p = m.map_to_insured(
        {"account_name": "Acme Plumbing LLC", "fein": "12-3456789", "city": "Atlanta",
         "state": "GA", "email": "a@b.com", "phone": "4045550142", "zip": "30301"},
        insured_type="Commercial",
    )
    assert p["CommercialName"] == "Acme Plumbing LLC"
    assert p["insuredType"] == "0"          # commercial code
    assert p["type"] == 1                    # prospect code (default)
    assert p["FEIN"] == "12-3456789"
    assert p["Zip"] == "30301"               # Zip, not ZipCode
    assert p["PhoneNumber"] == "4045550142"  # PhoneNumber, not CellPhone
    assert "FirstName" not in p
    assert "ProspectType" not in p and "InsuredType" not in p  # no raw keys


def test_map_personal_uses_first_last_and_code():
    p = m.map_to_insured(
        {"first_name": "Jane", "last_name": "Doe", "email": "jane@x.com"},
        insured_type="Personal",
    )
    assert p["FirstName"] == "Jane" and p["LastName"] == "Doe"
    assert "CommercialName" not in p
    assert p["insuredType"] == "1"           # personal code
    assert p["type"] == 1


def test_map_existing_insured_uses_type_zero():
    p = m.map_to_insured({"account_name": "Bound Co"}, insured_type="Commercial", is_prospect=False)
    assert p["type"] == 0                     # 0 kept (valid code, not "empty")


def test_map_drops_empty_fields():
    p = m.map_to_insured({"account_name": "Solo Co"}, insured_type="Commercial")
    assert "City" not in p and "FEIN" not in p and "Zip" not in p


def test_map_infers_insured_type_code_from_account():
    p = m.map_to_insured({"account_name": "Biz", "segment": "commercial"})
    assert p["insuredType"] == "0"
