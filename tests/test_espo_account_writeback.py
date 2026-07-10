"""Tests for hermes.jobs.espo_account_writeback — stub-on-won + fill-blank."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from hermes.jobs.espo_account_writeback import run_account_writeback


def fake_espo(*, opps=None, accounts_by_id=None, fill_accounts=None):
    espo = MagicMock()

    def _get(path, params=None):
        if path == "Opportunity":
            return {"list": list(opps or [])}
        if path == "Account":
            return {"list": list(fill_accounts or [])}
        if path.startswith("Account/"):
            return (accounts_by_id or {}).get(path.split("/", 1)[1], {})
        return {}

    espo.get.side_effect = _get
    return espo


class StubChannelTests(unittest.TestCase):
    def test_creates_stub_and_links_guid(self) -> None:
        opp = {"id": "O1", "stage": "Closed Won", "accountId": "A1", "modifiedAt": "2026-07-10 00:00:00"}
        acct = {"id": "A1", "name": "New Co LLC", "momentum_client_id": None, "emailAddress": "n@co.com"}
        espo = fake_espo(opps=[opp], accounts_by_id={"A1": acct})
        nc = MagicMock()
        nc.find_insured_id.return_value = None
        nc.insert_insured_no_override.return_value = {"insuredDatabaseId": "NEW-GUID", "status": 1}

        r = run_account_writeback(espo, nc, fill_blank=False)
        self.assertEqual(r.stubbed, 1)
        nc.insert_insured_no_override.assert_called_once()
        self.assertEqual(espo.patch.call_args.args[0], "Account/A1")
        self.assertEqual(espo.patch.call_args.kwargs["json"]["momentum_client_id"], "NEW-GUID")

    def test_links_existing_via_dedup_no_create(self) -> None:
        opp = {"id": "O1", "stage": "Closed Won", "accountId": "A1"}
        acct = {"id": "A1", "name": "Existing Co", "momentum_client_id": None, "emailAddress": "e@co.com"}
        espo = fake_espo(opps=[opp], accounts_by_id={"A1": acct})
        nc = MagicMock()
        nc.find_insured_id.return_value = "EXIST-GUID"

        r = run_account_writeback(espo, nc, fill_blank=False)
        self.assertEqual(r.linked_existing, 1)
        nc.insert_insured_no_override.assert_not_called()
        self.assertEqual(espo.patch.call_args.kwargs["json"]["momentum_client_id"], "EXIST-GUID")

    def test_skips_already_linked(self) -> None:
        opp = {"id": "O1", "stage": "Closed Won", "accountId": "A1"}
        acct = {"id": "A1", "name": "Linked Co", "momentum_client_id": "HAS-GUID"}
        espo = fake_espo(opps=[opp], accounts_by_id={"A1": acct})
        nc = MagicMock()

        r = run_account_writeback(espo, nc, fill_blank=False)
        self.assertEqual(r.already_linked, 1)
        nc.insert_insured_no_override.assert_not_called()
        espo.patch.assert_not_called()


class FillBlankTests(unittest.TestCase):
    def test_fills_only_blank_fields(self) -> None:
        acct = {"id": "A1", "name": "Acme", "momentum_client_id": "G1",
                "fein": "12-3456789", "emailAddress": None, "phoneNumber": "555-1000"}
        espo = fake_espo(fill_accounts=[acct])
        nc = MagicMock()
        # NowCerts insured: fein blank (fill it), eMail present (leave), phone blank (fill)
        nc._get.return_value = {"value": [{"id": "G1", "commercialName": "Acme",
                                           "fein": None, "eMail": "x@acme.com", "phone": None}]}

        r = run_account_writeback(espo, nc, fill_blank=True)
        self.assertEqual(r.filled, 1)
        self.assertEqual(r.fields_filled, 2)  # FEIN + Phone
        payload = nc.insert_insured_no_override.call_args.args[0]
        self.assertEqual(payload["DatabaseId"], "G1")
        self.assertEqual(payload["FEIN"], "12-3456789")
        self.assertEqual(payload["Phone"], "555-1000")
        self.assertNotIn("EMail", payload)  # already populated in AMS -> never overwrite

    def test_safety_skips_on_id_mismatch(self) -> None:
        acct = {"id": "A1", "name": "Acme", "momentum_client_id": "G1", "fein": "12-3456789"}
        espo = fake_espo(fill_accounts=[acct])
        nc = MagicMock()
        nc._get.return_value = {"value": [{"id": "DIFFERENT-GUID", "commercialName": "Acme", "fein": None}]}

        r = run_account_writeback(espo, nc, fill_blank=True)
        self.assertEqual(r.filled, 0)
        nc.insert_insured_no_override.assert_not_called()

    def test_noop_when_nothing_blank(self) -> None:
        acct = {"id": "A1", "name": "Acme", "momentum_client_id": "G1", "fein": "12-3456789"}
        espo = fake_espo(fill_accounts=[acct])
        nc = MagicMock()
        nc._get.return_value = {"value": [{"id": "G1", "commercialName": "Acme", "fein": "99-9999999"}]}

        r = run_account_writeback(espo, nc, fill_blank=True)
        self.assertEqual(r.fields_filled, 0)
        nc.insert_insured_no_override.assert_not_called()


if __name__ == "__main__":
    unittest.main()
