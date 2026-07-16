"""Tests for the intake write path: commit (pipeline + gated job + folder) and executor.

Supabase / NowCerts / Nextcloud mocked. Nothing writes to a live system.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermes.intake import commit as ic
from hermes.intake import executor as ie


def _router_supa():
    """supa whose insert returns per-table ids and select returns [] (fresh)."""
    counters = {"opportunities": 0, "outbound_sync_queue": 0}
    s = MagicMock()
    s.select.side_effect = lambda table, **kw: []
    def _insert(table, payload):
        counters[table] = counters.get(table, 0) + 1
        return {**payload, "id": f"{table[:3]}-{counters[table]}"}
    s.insert.side_effect = _insert
    return s


# ---------------------------------------------------------------- commit

def test_commit_creates_pipeline_job_and_folder():
    supa = _router_supa()
    nc = MagicMock()
    nc.is_configured.return_value = True
    nc.ensure_client_folders.return_value = "Clients/Acme Plumbing LLC"
    out = ic.commit_intake(
        supa,
        account={"account_name": "Acme Plumbing LLC", "fein": "12-3456789", "insured_type": "Commercial"},
        opportunities_spec=[{"line_of_business": "General Liability"}, {"line_of_business": "Workers Comp"}],
        approved_by="lamar",
        nextcloud=nc,
    )
    assert out["opportunity_count"] == 2
    assert out["intake_job_id"] == "out-1"
    assert out["nextcloud_folder"] == "Clients/Acme Plumbing LLC"
    assert out["insured_preview"]["CommercialName"] == "Acme Plumbing LLC"
    assert out["insured_preview"]["type"] == 1            # prospect code
    assert out["insured_preview"]["insuredType"] == "0"  # commercial code
    assert out["insured_type"] == "Commercial"


def test_commit_without_nextcloud_configured():
    supa = _router_supa()
    nc = MagicMock()
    nc.is_configured.return_value = False
    out = ic.commit_intake(
        supa, account={"account_name": "Solo Co", "insured_type": "Commercial"},
        opportunities_spec=[{"line_of_business": "BOP"}], approved_by="lamar", nextcloud=nc,
    )
    assert out["nextcloud_folder"] is None
    nc.ensure_client_folders.assert_not_called()


def test_commit_requires_opportunities_spec():
    with pytest.raises(ValueError):
        ic.commit_intake(MagicMock(), account={"name": "X"}, opportunities_spec=[], approved_by="lamar")


# ---------------------------------------------------------------- executor

def _job(insured=None, opp_ids=("opp-1",)):
    return {
        "id": "job-1",
        "payload": {
            "action": "create_insured",
            "insured": insured or {"CommercialName": "Acme", "ProspectType": "Prospect"},
            "opportunity_ids": list(opp_ids),
        },
    }


def test_executor_dry_run_previews_no_write():
    supa = MagicMock()
    supa.select.return_value = [_job()]
    nc = MagicMock()
    summary = ie.run_intake_executor(supa=supa, nowcerts=nc, dry_run=True)
    assert len(summary["previews"]) == 1
    nc.create_insured.assert_not_called()
    supa.update_where.assert_not_called()


def test_executor_commits_and_links_opportunity():
    supa = MagicMock()
    supa.select.return_value = [_job(opp_ids=("opp-1",))]
    supa.update_where.return_value = [{"id": "job-1"}]     # claim succeeds
    nc = MagicMock()
    nc.create_insured.return_value = {"databaseId": "ins-99"}
    summary = ie.run_intake_executor(supa=supa, nowcerts=nc)
    assert summary["completed"] == 1 and summary["failed"] == 0
    nc.create_insured.assert_called_once()
    # opportunity linked to the new insured id, then queue row completed.
    linked = [c for c in supa.update.call_args_list if c.args[0] == "opportunities"]
    assert linked and linked[0].args[2]["insured_id"] == "ins-99"


def test_executor_marks_failed_on_error():
    supa = MagicMock()
    supa.select.return_value = [_job()]
    supa.update_where.return_value = [{"id": "job-1"}]
    nc = MagicMock()
    nc.create_insured.side_effect = RuntimeError("boom")
    summary = ie.run_intake_executor(supa=supa, nowcerts=nc)
    assert summary["failed"] == 1
    fail = [c for c in supa.update.call_args_list if c.args[0] == "outbound_sync_queue"][-1]
    assert fail.args[2]["status"] == "failed"


def test_executor_no_jobs():
    supa = MagicMock()
    supa.select.return_value = []
    summary = ie.run_intake_executor(supa=supa, nowcerts=MagicMock())
    assert summary == {"claimed": 0, "completed": 0, "failed": 0, "previews": []}


# ---------------------------------------------------------------- commit_draft adapter

def test_commit_draft_maps_payload():
    supa = _router_supa()
    nc = MagicMock(); nc.is_configured.return_value = False
    out = ic.commit_draft(
        supa,
        {
            "account": {"account_name": "X LLC", "fein": "99-9999999", "account_type": "Commercial Lines"},
            "opportunities": [
                {"line_of_business": "General Liability", "premium": 5000},
                {"line_of_business": "Workers Comp"},
            ],
        },
        approved_by="lamar", nextcloud=nc,
    )
    assert out["opportunity_count"] == 2
    assert out["insured_type"] == "Commercial"
    assert out["insured_preview"]["CommercialName"] == "X LLC"


def test_commit_draft_infers_personal_from_lob():
    supa = _router_supa()
    nc = MagicMock(); nc.is_configured.return_value = False
    out = ic.commit_draft(
        supa,
        {"account": {"account_name": "Jane Doe Household"},
         "opportunities": [{"line_of_business": "Homeowners"}]},
        approved_by="lamar", nextcloud=nc,
    )
    assert out["insured_type"] == "Personal"


def test_commit_draft_requires_line_of_business():
    with pytest.raises(ValueError):
        ic.commit_draft(MagicMock(), {"account": {"name": "X"}, "opportunities": []}, approved_by="lamar")
