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

def test_commit_creates_pipeline_and_folder_without_touching_the_ams():
    """The default: an intake opens the pipeline and stages NOTHING for NowCerts.

    An intake is a prospect, and a prospect is not a record of insurance — the
    insured reaches the AMS when a deal on it is won. The insured payload is still
    previewed so an operator can see what *would* go, but no queue row exists.
    """
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
    assert out["intake_job_id"] is None
    assert out["ams_insured_staged"] is False
    # The load-bearing assertion: nothing was queued for NowCerts.
    assert [c.args[0] for c in supa.insert.call_args_list] == ["opportunities", "opportunities"]
    assert out["nextcloud_folder"] == "Clients/Acme Plumbing LLC"
    assert out["insured_preview"]["CommercialName"] == "Acme Plumbing LLC"
    assert out["insured_preview"]["type"] == 1            # prospect code
    assert out["insured_preview"]["insuredType"] == "0"  # commercial code
    assert out["insured_type"] == "Commercial"


def test_commit_stages_the_ams_insured_when_explicitly_opted_in(monkeypatch):
    """The opt-in path still works — migrations and backfills need it."""
    monkeypatch.setenv(ic.ENV_STAGE_AMS_INSURED, "1")
    supa = _router_supa()
    nc = MagicMock()
    nc.is_configured.return_value = False
    out = ic.commit_intake(
        supa,
        account={"account_name": "Acme Plumbing LLC", "fein": "12-3456789", "insured_type": "Commercial"},
        opportunities_spec=[{"line_of_business": "General Liability"}],
        approved_by="lamar",
        nextcloud=nc,
    )
    assert out["ams_insured_staged"] is True
    assert out["intake_job_id"] == "out-1"
    queued = [c.args[1] for c in supa.insert.call_args_list if c.args[0] == "outbound_sync_queue"]
    assert len(queued) == 1
    assert queued[0]["payload"]["action"] == "create_insured"


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "yes please", "TRUE-ish"])
def test_only_an_explicit_yes_stages_the_ams_insured(monkeypatch, value):
    """Anything ambiguous reads as off — guessing wrong puts prospects in the AMS."""
    monkeypatch.setenv(ic.ENV_STAGE_AMS_INSURED, value)
    assert ic.stages_ams_insured() is False


def test_unset_flag_stages_nothing(monkeypatch):
    monkeypatch.delenv(ic.ENV_STAGE_AMS_INSURED, raising=False)
    assert ic.stages_ams_insured() is False


def test_zoho_write_off_by_default(monkeypatch):
    monkeypatch.delenv(ic.ENV_WRITE_TO_ZOHO, raising=False)
    assert ic.writes_to_zoho() is False
    monkeypatch.setenv(ic.ENV_WRITE_TO_ZOHO, "false")
    assert ic.writes_to_zoho() is False


def test_zoho_write_runs_after_supabase_when_enabled(monkeypatch):
    """Zoho is additive: opportunity inserts happen first; Zoho failure stays non-fatal."""
    monkeypatch.setenv(ic.ENV_WRITE_TO_ZOHO, "1")
    supa = _router_supa()
    nc = MagicMock()
    nc.is_configured.return_value = True
    nc.ensure_client_folders.return_value = "Clients/Acme Plumbing LLC"

    zoho_calls: list[dict] = []

    def _fake_zoho(intake_payload, approved_by=None, **kwargs):
        zoho_calls.append({"payload": intake_payload, "approved_by": approved_by})
        return {
            "zoho_account_id": "z-acc-1",
            "zoho_deal_ids": ["z-deal-1", "z-deal-2"],
            "zoho_contact_ids": ["z-c-1"],
            "errors": [],
        }

    monkeypatch.setattr("hermes.intake.zoho_writer.write_intake_to_zoho", _fake_zoho)

    out = ic.commit_intake(
        supa,
        account={"account_name": "Acme Plumbing LLC", "fein": "12-3456789", "insured_type": "Commercial"},
        opportunities_spec=[
            {"line_of_business": "General Liability"},
            {"line_of_business": "Workers Comp"},
        ],
        approved_by="lamar",
        nextcloud=nc,
        intake_payload={
            "account": {"account_name": "Acme Plumbing LLC", "fein": "12-3456789"},
            "contacts": [{"full_name": "Pat Owner", "email": "pat@acme.test"}],
            "opportunities": [
                {"line_of_business": "General Liability", "opportunity_name": "Acme - GL"},
                {"line_of_business": "Workers Comp", "opportunity_name": "Acme - WC"},
            ],
            "note": {"title": "Intake", "body": "New commercial prospect"},
        },
    )

    # Supabase still wrote both opportunities first.
    assert [c.args[0] for c in supa.insert.call_args_list] == ["opportunities", "opportunities"]
    assert out["opportunity_count"] == 2
    assert len(zoho_calls) == 1
    assert zoho_calls[0]["approved_by"] == "lamar"
    assert zoho_calls[0]["payload"]["nextcloud_folder"] == "Clients/Acme Plumbing LLC"
    assert out["zoho"]["zoho_account_id"] == "z-acc-1"

    # Zoho ids stamped onto every opportunity row.
    updates = [c for c in supa.update.call_args_list if c.args[0] == "opportunities"]
    zoho_stamps = [c.args[2] for c in updates if "zoho_account_id" in c.args[2]]
    assert len(zoho_stamps) == 2
    assert all(s["zoho_account_id"] == "z-acc-1" for s in zoho_stamps)
    assert all(s["zoho_deal_ids"] == ["z-deal-1", "z-deal-2"] for s in zoho_stamps)


def test_zoho_payload_gets_https_folder_url_not_a_relative_path(monkeypatch):
    """Zoho URL fields need a Files-app link. The relative path stays operational-only."""
    monkeypatch.setenv(ic.ENV_WRITE_TO_ZOHO, "1")
    supa = _router_supa()
    nc = MagicMock()
    nc.is_configured.return_value = True
    nc.ensure_client_folders.return_value = "Clients/Acme Plumbing LLC"
    nc.browser_dir_url.return_value = "https://nc.example/apps/files/?dir=/Clients/Acme%20Plumbing%20LLC"
    nc.client_category_url.return_value = (
        "https://nc.example/apps/files/?dir=/Clients/Acme%20Plumbing%20LLC/Quotes"
    )

    zoho_calls: list[dict] = []

    def _fake_zoho(intake_payload, approved_by=None, **kwargs):
        zoho_calls.append(intake_payload)
        return {"zoho_account_id": "z-acc-1", "zoho_deal_ids": [], "zoho_contact_ids": [], "errors": []}

    monkeypatch.setattr("hermes.intake.zoho_writer.write_intake_to_zoho", _fake_zoho)
    out = ic.commit_intake(
        supa,
        account={"account_name": "Acme Plumbing LLC", "insured_type": "Commercial"},
        opportunities_spec=[{"line_of_business": "GL"}],
        approved_by="lamar",
        nextcloud=nc,
    )
    payload = zoho_calls[0]
    assert payload["nextcloud_folder"] == "Clients/Acme Plumbing LLC"
    assert payload["nextcloud_folder_url"].startswith("https://")
    assert payload["deal_primary_folder_url"].endswith("/Quotes")
    assert out["nextcloud_folder_url"].startswith("https://")


def test_zoho_payload_prefers_fileid_permalink(monkeypatch):
    monkeypatch.setenv(ic.ENV_WRITE_TO_ZOHO, "1")
    supa = _router_supa()
    nc = MagicMock()
    nc.is_configured.return_value = True
    nc.ensure_client_folders.return_value = "Clients/Berrios, Edwin"
    nc.open_dir_url.return_value = "https://nc.example/f/1842"
    nc.get_fileid.return_value = "1842"
    nc.browser_dir_url.return_value = "https://nc.example/apps/files/files?dir=/Clients/Berrios, Edwin"
    nc.client_category_url.return_value = (
        "https://nc.example/apps/files/files?dir=/Clients/Berrios, Edwin/Quotes"
    )

    zoho_calls: list[dict] = []

    def _fake_zoho(intake_payload, approved_by=None, **kwargs):
        zoho_calls.append(intake_payload)
        return {"zoho_account_id": "z-acc-1", "zoho_deal_ids": [], "zoho_contact_ids": [], "errors": []}

    monkeypatch.setattr("hermes.intake.zoho_writer.write_intake_to_zoho", _fake_zoho)
    out = ic.commit_intake(
        supa,
        account={"account_name": "Berrios, Edwin", "insured_type": "Personal"},
        opportunities_spec=[{"line_of_business": "HO3"}],
        approved_by="lamar",
        nextcloud=nc,
    )
    payload = zoho_calls[0]
    assert payload["nextcloud_folder_url"] == "https://nc.example/f/1842"
    assert payload["nextcloud_file_id"] == "1842"
    assert out["nextcloud_file_id"] == "1842"


def test_map_account_writes_text_link_not_website_field():
    from hermes_integrations.zoho_client import ZohoClient

    client = object.__new__(ZohoClient)
    mapped = client._map_account(
        {
            "account_name": "Berrios, Edwin",
            "nextcloud_folder_url": "https://nc.example/f/1842",
            "nextcloud_file_id": "1842",
        }
    )
    assert mapped["Nextcloud_Folder_Link"] == "https://nc.example/f/1842"
    assert mapped["Nextcloud_File_ID"] == "1842"
    assert "Nextcloud_Folder_URL" not in mapped



def test_account_block_drops_relative_nextcloud_path():
    from hermes.intake.zoho_writer import _account_block

    dropped = _account_block(
        {"account": {"account_name": "Acme"}, "nextcloud_folder": "Clients/Acme"}
    )
    assert "nextcloud_folder_url" not in dropped
    kept = _account_block(
        {
            "account": {"account_name": "Acme"},
            "nextcloud_folder_url": "https://nc.example/apps/files/?dir=/Clients/Acme",
        }
    )
    assert kept["nextcloud_folder_url"].startswith("https://")


def test_zoho_write_failure_does_not_break_supabase_commit(monkeypatch):
    monkeypatch.setenv(ic.ENV_WRITE_TO_ZOHO, "true")
    supa = _router_supa()
    nc = MagicMock()
    nc.is_configured.return_value = False

    def _boom(*args, **kwargs):
        raise RuntimeError("zoho down")

    monkeypatch.setattr("hermes.intake.zoho_writer.write_intake_to_zoho", _boom)

    out = ic.commit_intake(
        supa,
        account={"account_name": "Solo Co", "insured_type": "Commercial"},
        opportunities_spec=[{"line_of_business": "BOP"}],
        approved_by="lamar",
        nextcloud=nc,
    )
    assert out["opportunity_count"] == 1
    assert out["zoho"]["zoho_account_id"] is None
    assert out["zoho"]["errors"]
    # Supabase insert still happened; no Zoho stamp updates.
    assert [c.args[0] for c in supa.insert.call_args_list] == ["opportunities"]


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


def test_commit_ams_first_adopts_guid_via_gateway(monkeypatch):
    """When the gateway reports an adoptable insured, CRM rows key on that GUID."""
    monkeypatch.setenv("INTAKE_GATEWAY_URL", "http://gateway.test")
    monkeypatch.setenv("HERMES_INTAKE_AMS_FIRST", "1")

    def fake_create(account, approved_by):
        return {"ok": True, "adopted": True, "insured_database_id": "GUID-9", "reason": "name+email"}

    monkeypatch.setattr("hermes.intake.gateway_ams.create_or_adopt_insured", fake_create)
    supa = _router_supa()
    nc = MagicMock()
    nc.is_configured.return_value = False
    out = ic.commit_intake(
        supa,
        account={"account_name": "Acme Plumbing LLC", "email": "a@b.c", "insured_type": "Commercial"},
        opportunities_spec=[{"line_of_business": "General Liability"}],
        approved_by="lamar",
        nextcloud=nc,
    )
    assert out["nowcerts_insured_guid"] == "GUID-9"
    assert out["ams_gateway"]["adopted"] is True
    assert out["ams_insured_staged"] is False
