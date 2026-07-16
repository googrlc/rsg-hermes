"""Tests for renewal cases + tasks on the SHARED agency CRM schema (#113).

Renewal cases -> agency_crm_cases (case_type='renewal') + renewal_case_details
identity; tasks -> agency_crm_tasks. Supabase + NowCerts mocked.
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
    want = detail.get("policyNumber")
    nc.find_policy_by_number.side_effect = lambda n: (detail if n == want else None)
    nc.is_insured_active.return_value = True
    return nc


def _candidate(policy_number="TST-0001"):
    return {
        "insured_id": "ins-1", "policy_lineage_id": "lin-1", "renewal_event_date": "2027-01-01",
        "client_name": "Acme LLC", "segment": "commercial_small", "policy_number": policy_number,
    }


def _supa(tables=None):
    """Router: select returns tables[name]; insert returns row w/ per-table id."""
    tables = tables or {}
    counters: dict[str, int] = {}
    supa = MagicMock()
    supa.select.side_effect = lambda table, **kw: tables.get(table, [])

    def _insert(table, payload):
        counters[table] = counters.get(table, 0) + 1
        return {**payload, "id": f"{table.split('_')[-1][:4]}-{counters[table]}"}

    supa.insert.side_effect = _insert
    return supa


# ============================================================ library

def test_default_tasks_shape():
    ts = cases.default_tasks("gretchen@x.com")
    assert len(ts) == 5
    assert ts[0]["assigned_to_email"] == "gretchen@x.com"
    assert "title" in ts[0] and "description" in ts[0]


def test_create_case_requires_identity():
    with pytest.raises(ValueError):
        cases.create_case(MagicMock(), insured_id="", policy_lineage_id="l", renewal_event_date="2027-01-01")


def test_create_case_targets_agency_crm_and_details():
    supa = _supa({"renewal_case_details": [], "agency_crm_cases": []})
    row, created = cases.create_case(
        supa, insured_id="i", policy_lineage_id="l", renewal_event_date="2027-01-01",
        policy_number="TST-0001", client_name="Acme LLC",
    )
    assert created is True
    inserted_tables = [c.args[0] for c in supa.insert.call_args_list]
    assert "agency_crm_cases" in inserted_tables and "renewal_case_details" in inserted_tables
    # audit event emitted to the shared timeline
    assert "agency_crm_case_events" in inserted_tables
    event_payload = next(c.args[1] for c in supa.insert.call_args_list if c.args[0] == "agency_crm_case_events")
    assert event_payload["event_type"] == "case_created"
    case_payload = next(c.args[1] for c in supa.insert.call_args_list if c.args[0] == "agency_crm_cases")
    assert case_payload["case_type"] == "renewal"
    assert case_payload["insured_database_id"] == "i"
    assert case_payload["status"] == "open"
    assert case_payload["case_number"] == "REN-TST0001-20270101"  # required, generated


def test_renewal_case_number_is_deterministic():
    n1 = cases.renewal_case_number("TST-0001", "lin-1", "2027-01-01")
    n2 = cases.renewal_case_number("TST-0001", "lin-1", "2027-01-01")
    assert n1 == n2 == "REN-TST0001-20270101"
    # falls back to lineage when no policy number
    assert cases.renewal_case_number(None, "lin-9", "2027-06-15") == "REN-LIN9-20270615"


def test_create_case_idempotent_via_identity():
    supa = _supa({
        "renewal_case_details": [{"case_id": "case-9"}],
        "agency_crm_cases": [{"id": "case-9", "policy_number": "TST-0001"}],
    })
    row, created = cases.create_case(
        supa, insured_id="i", policy_lineage_id="l", renewal_event_date="2027-01-01"
    )
    assert created is False and row["id"] == "case-9"
    supa.insert.assert_not_called()


def test_create_tasks_targets_agency_crm_tasks_and_skips_dupes():
    supa = _supa({"agency_crm_tasks": [{"title": "A"}]})
    made = cases.create_tasks(supa, case_id="c1", tasks=[{"title": "A"}, {"title": "B"}])
    assert len(made) == 1 and made[0]["title"] == "B"
    ins = supa.insert.call_args
    assert ins.args[0] == "agency_crm_tasks"
    assert ins.args[1]["status"] == "not_started"


# ============================================================ handlers

def test_create_case_handle_opens_case_and_tasks():
    supa = _supa({"renewal_candidates": [_candidate()], "renewal_case_details": [],
                  "agency_crm_cases": [], "agency_crm_tasks": []})
    r = rc.create_case_handle(
        None, "create a renewal case and tasks for policy TST-0001", supa=supa, nowcerts=_nc(_detail())
    )
    assert r.ok and r.data["created"] is True
    assert r.data["tasks_created"] == 5


def test_create_case_handle_existing():
    supa = _supa({
        "renewal_candidates": [_candidate()],
        "renewal_case_details": [{"case_id": "case-9"}],
        "agency_crm_cases": [{"id": "case-9", "policy_number": "TST-0001", "status": "open",
                              "owner_email": "gretchen@x.com", "insured_name": "Acme LLC"}],
        "agency_crm_tasks": [],
    })
    r = rc.create_case_handle(None, "create a renewal case for policy TST-0001", supa=supa, nowcerts=_nc(_detail()))
    assert r.ok and r.data["created"] is False


def test_create_case_handle_needs_identifier():
    r = rc.create_case_handle(None, "create a renewal case", supa=MagicMock(), nowcerts=_nc(_detail()))
    assert not r.ok and r.data.get("need_identifier") is True


def test_create_tasks_handle_seeds_defaults():
    supa = _supa({
        "renewal_candidates": [_candidate()],
        "renewal_case_details": [{"case_id": "case-9"}],
        "agency_crm_cases": [{"id": "case-9", "policy_number": "TST-0001", "owner_email": "g@x.com"}],
        "agency_crm_tasks": [],
    })
    r = rc.create_tasks_handle(None, "create renewal tasks for policy TST-0001", supa=supa, nowcerts=_nc(_detail()))
    assert r.ok and r.data["tasks_created"] == 5


# ============================================================ routing precedence

def test_create_renewal_case_beats_data_entry():
    with patch("hermes.commands.renewal_cases.create_case_handle") as h:
        h.return_value = DispatchResult(True, "case")
        _make_dispatcher().dispatch(MagicMock(), "create a renewal case and tasks for policy TST-0001")
        h.assert_called_once()


def test_create_renewal_tasks_routes():
    with patch("hermes.commands.renewal_cases.create_tasks_handle") as h:
        h.return_value = DispatchResult(True, "tasks")
        _make_dispatcher().dispatch(MagicMock(), "create renewal tasks for policy TST-0001")
        h.assert_called_once()
