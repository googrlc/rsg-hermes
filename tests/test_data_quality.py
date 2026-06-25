from __future__ import annotations

import unittest
from datetime import date, timedelta

from hermes.commands.data_quality import AUDIT_RULES, handle
from hermes.commands.reports import handle as reports_handle
from hermes.commands.revenue import handle as revenue_handle
from hermes.core.client import EspoClientError


class SizeGuardClient:
    def get(self, entity: str, **kwargs):
        params = kwargs.get("params") or {}
        if int(params.get("maxSize", 0)) > 200:
            raise EspoClientError(f"403 GET {entity}: maxSize too large")
        return {"list": [{"id": "1", "name": "Acme"}], "total": 1}


class DataQualityTests(unittest.TestCase):
    def test_audit_rules_use_live_espo_field_names_for_custom_policy_and_account_fields(self) -> None:
        account_fields = {rule["field"] for rule in AUDIT_RULES["Account"]}
        policy_fields = {rule["field"] for rule in AUDIT_RULES["Policy"]}

        self.assertIn("account_status", account_fields)
        self.assertIn("policy_number", policy_fields)
        self.assertIn("effective_date", policy_fields)
        self.assertIn("premium_amount", policy_fields)
        self.assertIn("accountId", policy_fields)

    def test_data_quality_scan_uses_espo_safe_page_size(self) -> None:
        result = handle(SizeGuardClient(), "data quality")

        self.assertTrue(result.ok)
        self.assertIn("Overall score", result.message)
        self.assertNotIn("error scanning", result.message)
        self.assertEqual(result.data["scan_errors"], 0)

    def test_data_quality_policy_scan_paginates_beyond_first_page(self) -> None:
        class Client:
            def get(self, entity: str, **kwargs):
                params = kwargs.get("params") or {}
                max_size = int(params.get("maxSize", 0))
                offset = int(params.get("offset", 0))

                if max_size > 200:
                    raise EspoClientError(f"403 GET {entity}: maxSize too large")

                if entity != "Policy":
                    return {"list": [], "total": 0}

                total = 201
                if max_size == 1 and "offset" not in params:
                    return {"list": [], "total": total}

                rows = [
                    {
                        "id": f"p{i}",
                        "name": f"Policy {i}",
                        "policy_number": None,
                        "accountId": None,
                        "carrier": "Progressive",
                        "effective_date": "2026-01-01",
                        "premium_amount": "1000",
                    }
                    for i in range(offset, min(offset + max_size, total))
                ]
                return {"list": rows, "total": total}

        result = handle(Client(), "data quality")
        policy_rules = {
            rule["field"]: rule["violation_count"]
            for rule in result.data["entities"]["Policy"]["rules"]
        }

        self.assertEqual(policy_rules["policy_number"], 201)
        self.assertEqual(policy_rules["accountId"], 201)

    def test_stale_leads_uses_simple_list_read_and_filters_locally(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.params = None

            def get(self, entity: str, **kwargs):
                self.params = kwargs.get("params")
                return {
                    "list": [
                        {
                            "id": "1",
                            "name": "Old Open",
                            "stage": "Qualification",
                            "accountName": "Acme",
                            "modifiedAt": "2026-01-01 00:00:00",
                        },
                        {
                            "id": "2",
                            "name": "Old Closed",
                            "stage": "Closed Won",
                            "modifiedAt": "2026-01-01 00:00:00",
                        },
                    ],
                    "total": 2,
                }

        client = Client()
        result = reports_handle(client, "stale leads")

        self.assertTrue(result.ok)
        self.assertIn("Old Open", result.message)
        self.assertNotIn("Old Closed", result.message)
        self.assertNotIn("where", client.params)

    def test_renewal_sentinel_flags_90_day_policy_without_completed_review_task(self) -> None:
        exp_flagged = (date.today() + timedelta(days=30)).isoformat()
        exp_reviewed = (date.today() + timedelta(days=45)).isoformat()

        class Client:
            def get(self, entity: str, **kwargs):
                if entity == "Policy":
                    return {
                        "list": [
                            {
                                "id": "p1",
                                "name": "Atlas Auto",
                                "accountId": "a1",
                                "accountName": "Atlas Protection Group",
                                "line_of_business": "Commercial Auto",
                                "carrier": "Progressive",
                                "premium_amount": "25000",
                                "expiration_date": exp_flagged,
                                "status": "Active",
                            },
                            {
                                "id": "p2",
                                "name": "Reviewed WC",
                                "accountId": "a2",
                                "accountName": "Reviewed Co",
                                "line_of_business": "Workers Comp",
                                "expiration_date": exp_reviewed,
                                "status": "Active",
                            },
                        ],
                        "total": 2,
                    }
                if entity == "Task":
                    return {
                        "list": [
                            {
                                "id": "t1",
                                "name": "Renewal Review - Reviewed Co",
                                "status": "Completed",
                                "accountId": "a2",
                            }
                        ],
                        "total": 1,
                    }
                return {"list": [], "total": 0}

        result = revenue_handle(Client(), "renewals")

        self.assertTrue(result.ok)
        self.assertIn("Retention Risk", result.message)
        self.assertIn("Atlas Protection Group", result.message)
        self.assertNotIn("Reviewed Co", result.message)


if __name__ == "__main__":
    unittest.main()
