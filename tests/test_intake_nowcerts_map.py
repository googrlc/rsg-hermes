"""Tests for hermes/intake/nowcerts_map.py — intake account -> NowCerts Insured."""

from __future__ import annotations

from hermes.intake import nowcerts_map as m


def test_normalize_insured_type():
    assert m.normalize_insured_type("commercial lines") == "Commercial"
    assert m.normalize_insured_type("Home") == "Personal"
    assert m.normalize_insured_type("Commercial") == "Commercial"
    assert m.normalize_insured_type("") is None


def test_normalize_prospect_type():
    assert m.normalize_prospect_type("hot") == "Hot_Prospect"
    assert m.normalize_prospect_type("cold") == "Cold_Prospect"
    assert m.normalize_prospect_type(None) == "Prospect"
    assert m.normalize_prospect_type("weird") == "Prospect"


def test_map_commercial_uses_commercial_name():
    p = m.map_to_insured(
        {"account_name": "Acme Plumbing LLC", "fein": "12-3456789", "city": "Atlanta",
         "state": "GA", "email": "a@b.com"},
        insured_type="Commercial",
    )
    assert p["CommercialName"] == "Acme Plumbing LLC"
    assert p["InsuredType"] == "Commercial"
    assert p["ProspectType"] == "Prospect"
    assert p["FEIN"] == "12-3456789"
    assert "FirstName" not in p


def test_map_personal_uses_first_last():
    p = m.map_to_insured(
        {"first_name": "Jane", "last_name": "Doe", "email": "jane@x.com"},
        insured_type="Personal", prospect_type="hot",
    )
    assert p["FirstName"] == "Jane" and p["LastName"] == "Doe"
    assert "CommercialName" not in p
    assert p["InsuredType"] == "Personal"
    assert p["ProspectType"] == "Hot_Prospect"


def test_map_drops_empty_fields():
    p = m.map_to_insured({"account_name": "Solo Co"}, insured_type="Commercial")
    # No blank keys sent (would clobber existing NowCerts data).
    assert all(v not in (None, "") for v in p.values())
    assert "City" not in p and "FEIN" not in p


def test_map_infers_insured_type_from_account():
    p = m.map_to_insured({"account_name": "Biz", "segment": "commercial"})
    assert p["InsuredType"] == "Commercial"
