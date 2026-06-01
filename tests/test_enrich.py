"""Tests for the single-account NowCerts enrichment syncback."""
from unittest.mock import MagicMock

from hermes.sync.enrich import enrich_insured_from_account


def _espo(account):
    e = MagicMock()
    e.get.return_value = account
    return e


def _active_account(**over):
    a = {
        "id": "acc1",
        "lifecycle_status": "Active",
        "momentum_client_id": "nc-guid-1",
        "name": "Acme Corp",
        "emailAddress": "a@acme.com",
        "phoneNumber": "+17707808848",
        "account_type": "Commercial Lines",
    }
    a.update(over)
    return a


def test_dry_run_active_linked_would_enrich():
    nc = MagicMock()
    res = enrich_insured_from_account(_espo(_active_account()), nc, "acc1", dry_run=True)
    assert res["ok"] and res["action"] == "would_enrich"
    assert res["nowcerts_database_id"] == "nc-guid-1"
    # upsert key must be present so NowCerts updates (never creates)
    assert res["payload"]["DatabaseId"] == "nc-guid-1"
    nc.create_insured.assert_not_called()


def test_live_active_linked_enriches_via_upsert():
    nc = MagicMock()
    nc.create_insured.return_value = {"insuredDatabaseId": "nc-guid-1"}
    res = enrich_insured_from_account(_espo(_active_account()), nc, "acc1", dry_run=False)
    assert res["ok"] and res["action"] == "enriched"
    nc.create_insured.assert_called_once()
    sent = nc.create_insured.call_args[0][0]
    assert sent["DatabaseId"] == "nc-guid-1"


def test_inactive_account_skipped():
    nc = MagicMock()
    res = enrich_insured_from_account(_espo(_active_account(lifecycle_status="Inactive")), nc, "acc1")
    assert res["action"] == "skip" and not res["ok"]
    nc.create_insured.assert_not_called()


def test_prospect_account_skipped():
    nc = MagicMock()
    res = enrich_insured_from_account(_espo(_active_account(lifecycle_status="Prospect")), nc, "acc1")
    assert res["action"] == "skip"
    nc.create_insured.assert_not_called()


def test_unlinked_account_skipped():
    nc = MagicMock()
    res = enrich_insured_from_account(_espo(_active_account(momentum_client_id="")), nc, "acc1")
    assert res["action"] == "skip" and "momentum_client_id" in res["reason"]
    nc.create_insured.assert_not_called()


def test_missing_account_skipped():
    nc = MagicMock()
    res = enrich_insured_from_account(_espo({}), nc, "acc1")
    assert res["action"] == "skip"
    nc.create_insured.assert_not_called()
