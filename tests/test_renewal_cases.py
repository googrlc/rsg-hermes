"""Tests for renewal cases + tasks: library, NL handlers, routing precedence.

Supabase + NowCerts mocked. Synthetic identifiers only.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes.commands import renewal_cases as rc
from hermes.core.dispatcher import DispatchResult, Dispatcher
from hermes.renewals import cases


def _make_dispatcher() -> Dispatcher:
    d = Dispatcher(use_openai=False)
    d.supa = MagicMock()
    return d


def _detail(*, number="TST-0001", guid="pol-guid-1", insured="ins-guid-1", lob="Commercial Auto"):
    return {
        "databaseId": guid, "insuredDatabaseId": insured, "policyNumber": number,
        "carrierName": "Test Carrier", "lineOfBusiness": lob,
        "effectiveDate": "2026-01-01", "expirationDate": "2027-01-01",
        "premium": 5000, "policyStatus": "Active",
    }


def _nc(detail):
    nc = MagicMock()
    want = detail.get("policyNumber") if isinstance(detail, dict) else None
    nc.find_policy_by_number.side_effect = lambda n: (detail if detail and n == want else None)
    nc.is_insured_active.return_value = True
    return nc


def _candidate(policy_number="TST-0001"):
    return {
        "insured_id": "ins-1", "policy_lineage_id": "lin-1", "renewal_event_date": "2027-01-01",
        "client_name": "Acme LLC", "segment": "commercial_small", "policy_number": policy_number,
    }


def _supa(tables=None, inserted_id="row-1"):
    tables = tables or {}
    supa = MagicMock()
    supa.select.side_effect = lambda table, **kw: tables.get(table, [])
    supa.insert.side_effect = lambda table, payload: {**payload, "id": inserted_id}
    return supa


# ============================================================ library

def test_default_tasks_count():
    assert len(cases.default_tasks()) == 5


def test_create_case_requires_identity():
    with pytest.raises(ValueError):
        cases.create_case(MagicMock(), insured_id="", policy_lineage_id="l", renewal_event_date="2027-01-01")


def test_create_case_idempotent_returns_existing():
    supa = _supa({"renewal_cases": [{"id": "case-9"}]})
    row, created = cases.create_case(
        supa, insured_id="i", policy_lineage_id="l", renewal_event_date="2027-01-01"
    )
    assert created is False and row["id"] == "case-9"
    supa.insert.assert_not_called()


def test_create_tasks_skips_existing_titles():
    supa = _supa({"renewal_tasks": [{"title": "A"}]})
    made = cases.create_tasks(supa, case_id="c1", tasks=[{"title": "A"}, {"title": "B"}])
    assert len(made) == 1 and made[0]["title"] == "B"


# ============================================================ handlers

def test_create_case_handle_opens_case_and_tasks():
    supa = _supa({"renewal_candidates": [_candidate()], "renewal_cases": [], "renewal_tasks": []})
    r = rc.create_case_handle(
        None, "create a renewal case and tasks for policy TST-0001", supa=supa, nowcerts=_nc(_detail())
    )
    assert r.ok and r.data["created"] is True
    assert r.data["tasks_created"] == 5


def test_create_case_handle_existing_no_tasks_word():
    supa = _supa({
        "renewal_candidates": [_candidate()],
        "renewal_cases": [{"id": "case-9", "policy_number": "TST-0001", "status": "open",
                           "assigned_to": "gretchen", "client_name": "Acme LLC"}],
        "renewal_tasks": [],
    })
    r = rc.create_case_handle(None, "create a renewal case for policy TST-0001", supa=supa, nowcerts=_nc(_detail()))
    assert r.ok and r.data["created"] is False
    assert r.data["tasks_created"] == 0


def test_create_case_handle_needs_identifier():
    r = rc.create_case_handle(None, "create a renewal case", supa=MagicMock(), nowcerts=_nc(_detail()))
    assert not r.ok and r.data.get("need_identifier") is True


def test_create_tasks_handle_seeds_defaults():
    supa = _supa({
        "renewal_candidates": [_candidate()],
        "renewal_cases": [{"id": "case-9", "policy_number": "TST-0001", "assigned_to": "gretchen", "status": "open"}],
        "renewal_tasks": [],
    })
    r = rc.create_tasks_handle(None, "create renewal tasks for policy TST-0001", supa=supa, nowcerts=_nc(_detail()))
    assert r.ok and r.data["tasks_created"] == 5


# ============================================================ routing precedence

def test_create_renewal_case_beats_data_entry():
    """'create a renewal case ...' must hit the case route, not data_entry's ^create."""
    with patch("hermes.commands.renewal_cases.create_case_handle") as h:
        h.return_value = DispatchResult(True, "case")
        _make_dispatcher().dispatch(MagicMock(), "create a renewal case and tasks for policy TST-0001")
        h.assert_called_once()


def test_create_renewal_tasks_routes():
    with patch("hermes.commands.renewal_cases.create_tasks_handle") as h:
        h.return_value = DispatchResult(True, "tasks")
        _make_dispatcher().dispatch(MagicMock(), "create renewal tasks for policy TST-0001")
        h.assert_called_once()
