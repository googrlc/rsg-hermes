"""Tests for hermes.book_sync.investigate — single-policy cross-system diff."""

from __future__ import annotations

from unittest.mock import MagicMock

from hermes.book_sync.investigate import (
    VERDICT_AMBIGUOUS,
    VERDICT_NO_MISMATCH,
    VERDICT_NOT_FOUND,
    VERDICT_STALE_MIRROR,
    investigate_policy,
)


def _nc_policy(**kw):
    base = {
        "databaseId": "pg-guid-1",
        "insuredDatabaseId": "ins-guid-1",
        "policyNumber": "990414352",
        "policyStatus": "Cancelled",
        "effectiveDate": "2026-06-10",
        "expirationDate": "2026-12-09",
        "premium": 2551.55,
        "carrierName": "PROGRESSIVE",
        "lineOfBusiness": "Personal Auto",
        "insuredCommercialName": "Steven Prak",
    }
    base.update(kw)
    return base


def _mirror_row(**kw):
    base = {
        "policy_guid": "mirror-1",
        "nowcerts_insured_guid": "ins-guid-1",
        "policy_number": "990414352",
        "lines_of_business": "Personal Auto",
        "carrier": "PROGRESSIVE",
        "status": "Active",
        "active": True,
        "effective_date": "2025-06-10",
        "expiration_date": "2026-12-10",
        "premium_amount": 4618.55,
        "sync_owner": "rsg-import",
        "renewed_policy": None,
    }
    base.update(kw)
    return base


class TestInvestigatePolicy:
    def test_stale_mirror_when_ams_cancelled_and_mirror_active(self):
        nc = MagicMock()
        nc.find_policy_by_number.return_value = _nc_policy()
        nc.is_insured_active.return_value = False

        supa = MagicMock()
        supa.select.side_effect = [
            [_mirror_row(), _mirror_row(status="Cancelled", active=False, expiration_date="2026-12-09")],
            [{"nowcerts_insured_guid": "ins-guid-1", "insured_name": "Steven Prak", "active": True}],
            [{"id": "c1", "policy_number": "990414352", "normalized_status": "Active",
              "policy_active": True, "insured_active": False, "eligibility_state": "excluded",
              "eligibility_reason": "insured is not active", "renewal_event_date": "2026-12-10",
              "expiration_date": "2026-12-10", "in_working_queue": False, "premium_current": 4618.55}],
            [],
            [],
            [],
        ]

        report = investigate_policy("990414352", client_name="Steven Prak", nowcerts=nc, supa=supa)

        assert report.verdict == VERDICT_STALE_MIRROR
        assert "sync_canonical_book" in {a.action for a in report.recommended_actions}
        assert "renewal_refresh" in {a.action for a in report.recommended_actions}
        assert report.client_name == "Steven Prak"
        assert report.ams["status"] == "Cancelled"

    def test_not_found_when_everywhere_empty(self):
        nc = MagicMock()
        nc.find_policy_by_number.return_value = None
        nc.is_insured_active.return_value = True

        supa = MagicMock()
        supa.select.side_effect = [[], [], [], [], []]

        report = investigate_policy("NO-SUCH-POLICY", nowcerts=nc, supa=supa)

        assert report.verdict == VERDICT_NOT_FOUND

    def test_ambiguous_when_ams_returns_multiple(self):
        nc = MagicMock()
        nc.find_policy_by_number.return_value = {
            "_ambiguous": True,
            "matches": [_nc_policy(), _nc_policy(databaseId="pg-guid-2")],
        }

        supa = MagicMock()
        supa.select.side_effect = [[], [], [], [], []]

        report = investigate_policy("990414352", nowcerts=nc, supa=supa)

        assert report.verdict == VERDICT_AMBIGUOUS
        assert report.ams["ambiguous"] is True

    def test_no_mismatch_when_systems_agree(self):
        nc = MagicMock()
        nc.find_policy_by_number.return_value = _nc_policy(policyStatus="Active")
        nc.is_insured_active.return_value = True

        supa = MagicMock()
        supa.select.side_effect = [
            [_mirror_row(status="Active", active=True)],
            [{"nowcerts_insured_guid": "ins-guid-1", "insured_name": "Steven Prak", "active": True}],
            [],
            [],
            [],
            [],
        ]

        report = investigate_policy("990414352", nowcerts=nc, supa=supa)

        assert report.verdict == VERDICT_NO_MISMATCH
        assert "agree" in report.summary.lower()

    def test_requires_policy_number(self):
        nc = MagicMock()
        supa = MagicMock()
        try:
            investigate_policy("", nowcerts=nc, supa=supa)
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_format_lines_includes_verdict(self):
        nc = MagicMock()
        nc.find_policy_by_number.return_value = None
        supa = MagicMock()
        supa.select.side_effect = [[], [], [], [], []]
        report = investigate_policy("X", nowcerts=nc, supa=supa)
        lines = report.format_lines()
        assert any("Verdict:" in ln for ln in lines)
