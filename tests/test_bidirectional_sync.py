"""Tests for bidirectional sync: EspoCRM ↔ Supabase ↔ NowCerts."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

from hermes.sync.field_mapper import (
    map_account_to_golden,
    map_account_to_insured,
    map_commission_to_nowcerts_policy,
    map_policy_to_commission,
)


# ---------------------------------------------------------------------------
# Reverse mapper tests: EspoCRM Account → NowCerts Insured
# ---------------------------------------------------------------------------


class MapAccountToInsuredTests(unittest.TestCase):
    """Test EspoCRM Account → NowCerts Insured payload mapping."""

    def test_basic_commercial_account(self):
        espo = {
            "id": "acc123",
            "name": "Acme Corp",
            "primaryFirstName": "John",
            "primaryLastName": "Doe",
            "fein": "12-3456789",
            "billingAddressStreet": "123 Main St",
            "billingAddressCity": "Atlanta",
            "billingAddressState": "GA",
            "billingAddressPostalCode": "30303",
            "emailAddress": "john@acme.com",
            "phoneNumber": "4045551234",
            "accountType": "Commercial Lines",
            "typeOfBusiness": "LLC",
        }
        result = map_account_to_insured(espo)
        self.assertEqual(result["CommercialName"], "Acme Corp")
        self.assertEqual(result["FirstName"], "John")
        self.assertEqual(result["LastName"], "Doe")
        self.assertEqual(result["FEIN"], "12-3456789")
        self.assertEqual(result["AddressLine1"], "123 Main St")
        self.assertEqual(result["City"], "Atlanta")
        self.assertEqual(result["State"], "GA")
        self.assertEqual(result["ZipCode"], "30303")
        self.assertEqual(result["EMail"], "john@acme.com")
        self.assertEqual(result["Phone"], "4045551234")
        self.assertEqual(result["Type"], "Commercial")
        self.assertEqual(result["TypeOfBusiness"], "LLC")
        self.assertTrue(result["Active"])

    def test_with_nowcerts_database_id(self):
        espo = {"name": "Test Co", "accountType": "Personal Lines"}
        result = map_account_to_insured(espo, nowcerts_database_id="nc-uuid-123")
        self.assertEqual(result["DatabaseId"], "nc-uuid-123")
        self.assertEqual(result["CommercialName"], "Test Co")
        self.assertEqual(result["Type"], "Personal")

    def test_empty_fields_excluded(self):
        espo = {"name": "Minimal", "fein": "", "emailAddress": None}
        result = map_account_to_insured(espo)
        self.assertEqual(result["CommercialName"], "Minimal")
        self.assertNotIn("FEIN", result)
        self.assertNotIn("EMail", result)

    def test_date_only_transform(self):
        espo = {
            "name": "Date Test",
            "dateOfBirth": "1985-07-14T00:00:00-05:00",
        }
        result = map_account_to_insured(espo)
        self.assertEqual(result["DateOfBirth"], "1985-07-14")

    def test_enum_map_group_benefits(self):
        espo = {"name": "Benefits Co", "accountType": "Group Benefits"}
        result = map_account_to_insured(espo)
        self.assertEqual(result["Type"], "Benefits")


# ---------------------------------------------------------------------------
# Golden record mapper tests
# ---------------------------------------------------------------------------


class MapAccountToGoldenTests(unittest.TestCase):
    """Test EspoCRM Account → crm_accounts (Supabase) row mapping."""

    def test_full_account(self):
        espo = {
            "id": "espo-123",
            "name": "Golden Corp",
            "primaryFirstName": "Jane",
            "primaryLastName": "Smith",
            "accountType": "Commercial Lines",
            "fein": "98-7654321",
            "billingAddressStreet": "456 Oak Ave",
            "billingAddressCity": "Savannah",
            "billingAddressState": "GA",
            "billingAddressPostalCode": "31401",
            "emailAddress": "jane@golden.com",
            "phoneNumber": "9125551234",
            "website": "golden.com",
            "typeOfBusiness": "Corp",
            "yearBusinessStarted": 2010,
            "momentum_client_id": "nc-uuid-456",
        }
        row = map_account_to_golden(espo)
        self.assertEqual(row["espocrm_id"], "espo-123")
        self.assertEqual(row["name"], "Golden Corp")
        self.assertEqual(row["first_name"], "Jane")
        self.assertEqual(row["fein"], "98-7654321")
        self.assertEqual(row["nowcerts_id"], "nc-uuid-456")
        self.assertEqual(row["source_system"], "espocrm")
        self.assertIs(row["raw_espo_payload"], espo)


# ---------------------------------------------------------------------------
# Commission mapper tests
# ---------------------------------------------------------------------------


class MapPolicyToCommissionTests(unittest.TestCase):
    """Test EspoCRM Policy → crm_commissions row."""

    def test_basic_policy(self):
        policy = {
            "id": "pol-123",
            "policyNumber": "WC-2026-001",
            "carrierName": "Hartford",
            "lineOfBusiness": "Workers Comp",
            "premium": 12500.00,
            "commissionRate": 15.0,
            "commissionAmount": 1875.00,
            "agencyFee": 250.00,
            "effectiveDate": "2026-01-01",
            "expirationDate": "2027-01-01",
            "status": "Active",
        }
        row = map_policy_to_commission(policy, account_id="acc-uuid")
        self.assertEqual(row["account_id"], "acc-uuid")
        self.assertEqual(row["policy_number"], "WC-2026-001")
        self.assertEqual(row["carrier"], "Hartford")
        self.assertEqual(row["premium"], 12500.00)
        self.assertEqual(row["commission_rate"], 15.0)
        self.assertEqual(row["commission_amount"], 1875.00)
        self.assertEqual(row["source_system"], "espocrm")


class MapCommissionToNowCertsPolicyTests(unittest.TestCase):
    """Test crm_commissions → NowCerts Policy payload."""

    def test_full_commission(self):
        comm = {
            "policy_number": "GL-001",
            "carrier": "Travelers",
            "line_of_business": "General Liability",
            "premium": 8000.00,
            "commission_rate": 12.5,
            "commission_amount": 1000.00,
            "agency_fee": 150.00,
            "effective_date": "2026-06-01",
            "expiration_date": "2027-06-01",
        }
        payload = map_commission_to_nowcerts_policy(comm, insured_database_id="nc-uuid")
        self.assertEqual(payload["InsuredDatabaseId"], "nc-uuid")
        self.assertEqual(payload["Number"], "GL-001")
        self.assertEqual(payload["CarrierName"], "Travelers")
        self.assertEqual(payload["LineOfBusinessName"], "General Liability")
        self.assertEqual(payload["Premium"], 8000.00)
        self.assertEqual(payload["AgencyCommissionPercent"], 12.5)
        self.assertEqual(payload["AgencyCommissionValue"], 1000.00)
        self.assertEqual(payload["AgencyFee"], 150.00)

    def test_minimal_commission(self):
        comm = {"policy_number": "AUTO-001"}
        payload = map_commission_to_nowcerts_policy(comm)
        self.assertEqual(payload["Number"], "AUTO-001")
        self.assertNotIn("InsuredDatabaseId", payload)
        self.assertNotIn("Premium", payload)


# ---------------------------------------------------------------------------
# NowCerts write client tests
# ---------------------------------------------------------------------------


class NowCertsWriteTests(unittest.TestCase):
    """Test NowCerts client write methods."""

    @patch("hermes.sync.nowcerts_client.requests")
    def test_create_insured(self, mock_requests):
        from hermes.sync.nowcerts_client import NowCertsClient

        # Mock auth
        auth_resp = MagicMock()
        auth_resp.ok = True
        auth_resp.json.return_value = {"access_token": "test-token"}

        # Mock POST
        post_resp = MagicMock()
        post_resp.ok = True
        post_resp.status_code = 200
        post_resp.content = b'{"DatabaseId": "new-nc-id"}'
        post_resp.json.return_value = {"DatabaseId": "new-nc-id"}

        mock_requests.post.side_effect = [auth_resp, post_resp]

        client = NowCertsClient(username="test", password="test")
        result = client.create_insured({"CommercialName": "New Corp"})
        self.assertEqual(result["DatabaseId"], "new-nc-id")

    @patch("hermes.sync.nowcerts_client.requests")
    def test_insert_policy(self, mock_requests):
        from hermes.sync.nowcerts_client import NowCertsClient

        auth_resp = MagicMock()
        auth_resp.ok = True
        auth_resp.json.return_value = {"access_token": "test-token"}

        post_resp = MagicMock()
        post_resp.ok = True
        post_resp.status_code = 200
        post_resp.content = b'{}'
        post_resp.json.return_value = {}

        mock_requests.post.side_effect = [auth_resp, post_resp]

        client = NowCertsClient(username="test", password="test")
        result = client.insert_policy({"Number": "WC-001", "Premium": 10000})
        self.assertEqual(result, {})

    @patch("hermes.sync.nowcerts_client.requests")
    def test_update_policy_patch(self, mock_requests):
        from hermes.sync.nowcerts_client import NowCertsClient

        auth_resp = MagicMock()
        auth_resp.ok = True
        auth_resp.json.return_value = {"access_token": "test-token"}

        patch_resp = MagicMock()
        patch_resp.ok = True
        patch_resp.status_code = 200
        patch_resp.content = b'{}'
        patch_resp.json.return_value = {}

        mock_requests.post.return_value = auth_resp
        mock_requests.patch.return_value = patch_resp

        client = NowCertsClient(username="test", password="test")
        result = client.update_policy({"DatabaseId": "pol-uuid", "Premium": 15000})
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# Bidirectional pipeline tests (mocked)
# ---------------------------------------------------------------------------


def _mock_supa():
    """Create a mock SupabaseClient with wrapper methods."""
    supa = MagicMock()
    supa.insert.return_value = {"id": "run-123"}
    supa.update.return_value = {}
    supa.update_where.return_value = []
    supa.select.return_value = []
    supa.upsert.return_value = {}
    return supa


def _mock_espo():
    """Create a mock EspoClient."""
    espo = MagicMock()
    return espo


def _mock_nc():
    """Create a mock NowCertsClient."""
    nc = MagicMock()
    nc.create_insured.return_value = {"DatabaseId": "new-nc-id"}
    nc.insert_policy.return_value = {}
    return nc


class CrmToHubTests(unittest.TestCase):
    """Test EspoCRM → Supabase mirror pipeline."""

    def test_mirror_accounts_dry_run(self):
        from hermes.sync.bidirectional import run_crm_to_hub

        espo = _mock_espo()
        espo.get.return_value = {
            "list": [
                {"id": "acc-1", "name": "Test Corp", "accountType": "Commercial Lines"},
                {"id": "acc-2", "name": "Personal Client", "accountType": "Personal Lines"},
            ]
        }
        supa = _mock_supa()

        result = run_crm_to_hub(espo, supa, dry_run=True, since_hours=24)
        self.assertTrue(result.ok)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.accounts_mirrored, 2)

    def test_mirror_handles_empty(self):
        from hermes.sync.bidirectional import run_crm_to_hub

        espo = _mock_espo()
        espo.get.return_value = {"list": []}
        supa = _mock_supa()

        result = run_crm_to_hub(espo, supa, dry_run=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.accounts_mirrored, 0)

    def test_mirror_handles_espo_error(self):
        from hermes.sync.bidirectional import run_crm_to_hub
        from hermes.core.client import EspoClientError

        espo = _mock_espo()
        espo.get.side_effect = EspoClientError("Connection refused")
        supa = _mock_supa()

        result = run_crm_to_hub(espo, supa, dry_run=True)
        # Should still complete (warnings logged)
        self.assertEqual(result.accounts_mirrored, 0)


class HubToNowCertsTests(unittest.TestCase):
    """Test Supabase → NowCerts push pipeline."""

    def test_push_unlinked_accounts_dry_run(self):
        from hermes.sync.bidirectional import run_hub_to_nowcerts

        nc = _mock_nc()
        supa = _mock_supa()
        # Return unlinked accounts from select (for _fetch_unlinked_accounts)
        supa.select.side_effect = lambda table, **kw: (
            [{"espocrm_id": "acc-1", "name": "New Corp", "raw_espo_payload": {"name": "New Corp", "primaryLastName": "Owner"}}]
            if table == "crm_accounts" else []
        )

        result = run_hub_to_nowcerts(nc, supa, dry_run=True)
        self.assertTrue(result.ok)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.accounts_pushed, 1)
        nc.create_insured.assert_not_called()

    def test_push_no_unlinked(self):
        from hermes.sync.bidirectional import run_hub_to_nowcerts

        nc = _mock_nc()
        supa = _mock_supa()

        result = run_hub_to_nowcerts(nc, supa, dry_run=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.accounts_pushed, 0)


class BidirectionalOrchestratorTests(unittest.TestCase):
    """Test full bidirectional orchestrator."""

    @patch("hermes.sync.bidirectional.run_hub_to_nowcerts")
    @patch("hermes.sync.bidirectional.run_crm_to_hub")
    @patch("hermes.sync.pipeline.run_insured_to_account_sync")
    def test_full_dry_run(self, mock_nc_sync, mock_crm_hub, mock_hub_nc):
        from hermes.sync.bidirectional import BidiSyncResult, run_bidirectional
        from hermes.sync.pipeline import SyncRunResult

        mock_nc_sync.return_value = SyncRunResult(
            run_id="run-1", records_created=3, records_updated=2, dry_run=True
        )
        mock_crm_hub.return_value = BidiSyncResult(
            run_id="run-2", accounts_mirrored=5, dry_run=True
        )
        mock_hub_nc.return_value = BidiSyncResult(
            run_id="run-3", accounts_pushed=1, dry_run=True
        )

        nc = _mock_nc()
        espo = _mock_espo()
        supa = _mock_supa()

        result = run_bidirectional(nc, espo, supa, dry_run=True)
        self.assertTrue(result.ok)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.direction, "bidirectional")
        self.assertEqual(result.accounts_mirrored, 10)  # 5 from NC sync + 5 from CRM hub
        self.assertEqual(result.accounts_pushed, 1)


# ---------------------------------------------------------------------------
# Sync command routing tests
# ---------------------------------------------------------------------------


class SyncCommandBidiTests(unittest.TestCase):
    """Test dispatcher routing for bidirectional sync commands."""

    @patch("hermes.commands.sync._trigger_bidirectional")
    def test_bidirectional_command(self, mock_bidi):
        from hermes.commands.sync import handle

        mock_bidi.return_value = MagicMock(ok=True, message="done")
        supa = MagicMock()
        handle(MagicMock(), "sync bidirectional", supa=supa)
        mock_bidi.assert_called_once()

    @patch("hermes.commands.sync._trigger_crm_to_hub")
    def test_crm_to_hub_command(self, mock_crm):
        from hermes.commands.sync import handle

        mock_crm.return_value = MagicMock(ok=True, message="done")
        supa = MagicMock()
        handle(MagicMock(), "sync crm-to-hub", supa=supa)
        mock_crm.assert_called_once()

    @patch("hermes.commands.sync._trigger_hub_to_nowcerts")
    def test_push_to_nowcerts_command(self, mock_push):
        from hermes.commands.sync import handle

        mock_push.return_value = MagicMock(ok=True, message="done")
        supa = MagicMock()
        handle(MagicMock(), "sync push-to-nowcerts", supa=supa)
        mock_push.assert_called_once()

    @patch("hermes.commands.sync._trigger_bidirectional")
    def test_full_sync_command(self, mock_bidi):
        from hermes.commands.sync import handle

        mock_bidi.return_value = MagicMock(ok=True, message="done")
        supa = MagicMock()
        handle(MagicMock(), "sync full-sync", supa=supa)
        mock_bidi.assert_called_once()


class BidiSyncResultTests(unittest.TestCase):
    """Test BidiSyncResult dataclass."""

    def test_ok_when_no_failures(self):
        from hermes.sync.bidirectional import BidiSyncResult
        r = BidiSyncResult(accounts_mirrored=5, accounts_pushed=2)
        self.assertTrue(r.ok)

    def test_not_ok_when_failures(self):
        from hermes.sync.bidirectional import BidiSyncResult
        r = BidiSyncResult(records_failed=1)
        self.assertFalse(r.ok)

    def test_message_format(self):
        from hermes.sync.bidirectional import BidiSyncResult
        r = BidiSyncResult(
            direction="bidirectional", accounts_mirrored=3,
            accounts_pushed=1, commissions_mirrored=2,
            dry_run=True,
        )
        msg = r.message
        self.assertIn("DRY RUN", msg)
        self.assertIn("bidirectional", msg)
        self.assertIn("accounts_mirrored=3", msg)


if __name__ == "__main__":
    unittest.main()
