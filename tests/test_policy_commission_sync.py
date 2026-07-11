"""Tests for the Policy→Commission mirror in hermes.sync.policy_sync."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from hermes.sync.policy_sync import run_policy_sync


def _policy(**overrides) -> dict:
    base = {
        "number": "CPK-1",
        "lineOfBusinesses": [{"lineOfBusinessName": "Commercial Auto"}],
        "effectiveDate": "2026-07-01T00:00:00",
        "carrierName": "Progressive Commercial",
        "agencyCommissionPercent": 12.5,
        "agencyCommissionValue": 1000.0,
        "insuredCommercialName": "Summit HVAC LLC",
    }
    base.update(overrides)
    return base


def _espo_for(existing_policy=None, existing_commission=None) -> MagicMock:
    espo = MagicMock()
    # Account resolves (policy links to an existing account).
    # Commission metadata exposes the policyId link so conform keeps it.
    espo.get_metadata.return_value = {
        "Commission": {"fields": {"name": {}, "carrier": {}, "commissionRate": {},
                                   "estimatedCommission": {}, "effectiveDate": {}},
                       "links": {"policy": {}, "account": {}}}
    }

    def _find_one(entity, field, value, **kw):
        if entity == "Account":
            return {"id": "ACC1", "name": "Summit HVAC LLC"}
        if entity == "Policy":
            return existing_policy
        if entity == "Commission":
            return existing_commission
        return None

    espo.find_one_by_field.side_effect = _find_one
    espo.create.return_value = {"id": "POL-NEW"}
    espo.update.return_value = {"id": "POL-EXIST"}
    return espo


class PolicyCommissionSyncTests(unittest.TestCase):
    def test_new_policy_creates_linked_commission(self):
        espo = _espo_for(existing_policy=None, existing_commission=None)
        nc = MagicMock()
        nc.fetch_policies.return_value = [_policy()]

        result = run_policy_sync(nc, espo, dry_run=False)

        self.assertEqual(result.created, 1)
        self.assertEqual(result.commissions_created, 1)
        # Commission created and linked to the freshly created Policy id.
        comm_calls = [c for c in espo.create.call_args_list if c.args[0] == "Commission"]
        self.assertEqual(len(comm_calls), 1)
        payload = comm_calls[0].args[1]
        self.assertEqual(payload["policyId"], "POL-NEW")
        self.assertEqual(payload["accountId"], "ACC1")
        self.assertEqual(payload["commissionRate"], 12.5)

    def test_existing_commission_is_updated_not_duplicated(self):
        espo = _espo_for(
            existing_policy={"id": "POL-EXIST", "policy_number": "CPK-1"},
            existing_commission={"id": "COMM-1"},
        )
        nc = MagicMock()
        nc.fetch_policies.return_value = [_policy()]

        result = run_policy_sync(nc, espo, dry_run=False)

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.commissions_updated, 1)
        self.assertEqual(result.commissions_created, 0)
        comm_updates = [c for c in espo.update.call_args_list if c.args[0] == "Commission"]
        self.assertEqual(len(comm_updates), 1)
        self.assertEqual(comm_updates[0].args[1], "COMM-1")

    def test_dry_run_writes_nothing(self):
        espo = _espo_for()
        nc = MagicMock()
        nc.fetch_policies.return_value = [_policy()]

        result = run_policy_sync(nc, espo, dry_run=True)

        self.assertEqual(result.commissions_created, 1)  # counted, not written
        espo.create.assert_not_called()
        espo.update.assert_not_called()

    def test_commission_skipped_when_metadata_lacks_policy_link(self):
        espo = _espo_for()
        # Commission metadata WITHOUT a policy link → policyId dropped by conform.
        espo.get_metadata.return_value = {
            "Commission": {"fields": {"name": {}, "carrier": {}}, "links": {"account": {}}}
        }
        nc = MagicMock()
        nc.fetch_policies.return_value = [_policy()]

        result = run_policy_sync(nc, espo, dry_run=False)

        self.assertEqual(result.created, 1)          # policy still synced
        self.assertEqual(result.commissions_created, 0)  # commission skipped, not orphaned
        self.assertFalse([c for c in espo.create.call_args_list if c.args[0] == "Commission"])


if __name__ == "__main__":
    unittest.main()
