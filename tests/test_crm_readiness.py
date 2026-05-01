from __future__ import annotations

import unittest

from hermes.core.auditor import crm_readiness
from hermes.core.client import EspoClientError


class FakeClient:
    def __init__(self) -> None:
        self.read_failures: set[str] = set()

    def ping(self):
        return {"id": "u1", "userName": "herm_assistant"}

    def get(self, path: str, **kwargs):
        if path in self.read_failures:
            raise EspoClientError(f"403 GET {path}: EspoCRM API user lacks permission")
        return {"list": [], "total": 7}

    def get_metadata(self, key: str | None = None):
        return {"entityDefs": {"Account": {}, "Contact": {}, "Opportunity": {}}}


class CrmReadinessTests(unittest.TestCase):
    def test_readiness_marks_core_entity_read_permission_as_critical(self) -> None:
        client = FakeClient()
        client.read_failures.add("Opportunity")

        report = crm_readiness(client)

        self.assertFalse(report.ok)
        self.assertEqual(report.failed_critical, 1)
        messages = "\n".join(check.message for check in report.checks)
        self.assertIn("Opportunity read failed", messages)
        self.assertIn("Account read ok", messages)
        self.assertIn("herm_assistant", messages)

    def test_readiness_passes_when_auth_metadata_and_core_reads_work(self) -> None:
        report = crm_readiness(FakeClient())

        self.assertTrue(report.ok)
        self.assertEqual(report.failed_critical, 0)
        self.assertGreaterEqual(len(report.checks), 5)


if __name__ == "__main__":
    unittest.main()
