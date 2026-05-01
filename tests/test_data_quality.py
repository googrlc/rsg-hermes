from __future__ import annotations

import unittest

from hermes.commands.data_quality import AUDIT_RULES, handle
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

    def test_data_quality_scan_uses_espo_safe_page_size(self) -> None:
        result = handle(SizeGuardClient(), "data quality")

        self.assertTrue(result.ok)
        self.assertIn("Overall score", result.message)
        self.assertNotIn("error scanning", result.message)
        self.assertEqual(result.data["scan_errors"], 0)


if __name__ == "__main__":
    unittest.main()
