"""Tests for the opportunities pipeline library (hermes/intake/opportunities.py).

Supabase mocked. Synthetic identifiers only.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermes.intake import opportunities as opp


def _supa(existing=None, inserted_id="opp-1"):
    s = MagicMock()
    s.select.return_value = existing or []
    s.insert.side_effect = lambda table, payload: {**payload, "id": inserted_id}
    s.update.side_effect = lambda table, rid, payload: {"id": rid, **payload}
    return s


# ---------------------------------------------------------------- helpers

def test_make_client_identifier():
    assert opp.make_client_identifier("Acme Plumbing LLC", "12-3456789") == "acme-plumbing-llc:123456789"
    assert opp.make_client_identifier("Acme Plumbing LLC") == "acme-plumbing-llc"
    assert opp.make_client_identifier("") == "unknown"


def test_status_for_stage():
    assert opp.status_for_stage(opp.STAGE_BOUND) == opp.STATUS_WON
    assert opp.status_for_stage(opp.STAGE_LOST) == opp.STATUS_LOST
    assert opp.status_for_stage(opp.STAGE_QUOTING) == opp.STATUS_OPEN


# ---------------------------------------------------------------- create

def test_create_opportunity_new():
    supa = _supa(existing=[])
    row, created = opp.create_opportunity(
        supa, client_identifier="acme:1", line_of_business="General Liability",
        insured_name="Acme", prospect_type="Hot_Prospect", insured_type="Commercial",
    )
    assert created is True
    assert row["stage"] == "New" and row["status"] == "open"
    assert row["prospect_type"] == "Hot_Prospect"
    supa.insert.assert_called_once()


def test_create_opportunity_idempotent():
    supa = _supa(existing=[{"id": "opp-9", "stage": "Quoting"}])
    row, created = opp.create_opportunity(
        supa, client_identifier="acme:1", line_of_business="General Liability"
    )
    assert created is False and row["id"] == "opp-9"
    supa.insert.assert_not_called()


def test_create_unknown_stage_raises():
    with pytest.raises(ValueError):
        opp.create_opportunity(MagicMock(), client_identifier="a", line_of_business="GL", stage="Bogus")


def test_create_requires_client_and_lob():
    with pytest.raises(ValueError):
        opp.create_opportunity(MagicMock(), client_identifier="", line_of_business="GL")


# ---------------------------------------------------------------- advance / link

def test_advance_stage_to_bound_is_won():
    supa = _supa()
    row = opp.advance_stage(supa, "opp-1", opp.STAGE_BOUND)
    assert row["stage"] == "Bound" and row["status"] == "won"


def test_advance_stage_to_lost_records_reason():
    supa = _supa()
    row = opp.advance_stage(supa, "opp-1", opp.STAGE_LOST, lost_reason="price")
    assert row["status"] == "lost" and row["lost_reason"] == "price"


def test_advance_unknown_stage_raises():
    with pytest.raises(ValueError):
        opp.advance_stage(MagicMock(), "opp-1", "Bogus")


def test_link_nowcerts_backfills_ids():
    supa = _supa()
    row = opp.link_nowcerts(supa, "opp-1", insured_id="ins-1", quote_number="QOUS-1")
    assert row["insured_id"] == "ins-1" and row["quote_number"] == "QOUS-1"


def test_link_nowcerts_noop_when_empty():
    supa = _supa()
    assert opp.link_nowcerts(supa, "opp-1") == {}
    supa.update.assert_not_called()


# ---------------------------------------------------------------- list

def test_list_opportunities_filters():
    supa = _supa(existing=[{"id": "o1"}])
    opp.list_opportunities(supa, stage="Quoting", assigned_to="gretchen")
    _, kw = supa.select.call_args
    params = kw["params"]
    assert params["stage"] == "eq.Quoting"
    assert params["status"] == "eq.open"
    assert params["assigned_to"] == "eq.gretchen"
