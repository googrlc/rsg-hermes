"""Tests for the EspoCRM read-only direct-Postgres lane (hermes/integrations/espo_db.py).

These cover the logic that needs no live database:
  - is_configured() reads the env vars
  - the SELECT/WITH-only write guard in _rows()
  - _merge() keeps the highest-scoring candidate per record
  - REQUIRED_SCHEMA is well-formed
  - DbHealth.format_lines() reflects ok/missing state
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from hermes.integrations import espo_db
from hermes.integrations.espo_db import (
    REQUIRED_SCHEMA,
    DbHealth,
    DuplicateCandidate,
    EspoDb,
    EspoDbError,
)


class IsConfiguredTests(unittest.TestCase):
    def test_false_when_env_absent(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(espo_db.is_configured())

    def test_true_when_required_vars_present(self) -> None:
        env = {"ESPO_DB_HOST": "h", "ESPO_DB_NAME": "d", "ESPO_DB_USER": "u"}
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(espo_db.is_configured())

    def test_false_when_partial(self) -> None:
        env = {"ESPO_DB_HOST": "h", "ESPO_DB_NAME": "d"}  # no user
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(espo_db.is_configured())


class WriteGuardTests(unittest.TestCase):
    def _db(self) -> EspoDb:
        return EspoDb(host="h", dbname="d", user="u", password="p")

    def test_rows_refuses_non_select(self) -> None:
        db = self._db()
        for bad in ("UPDATE account SET x=1", "DELETE FROM contact", "INSERT INTO lead VALUES (1)"):
            with self.assertRaises(EspoDbError):
                db._rows(bad, {})

    def test_init_requires_core_fields(self) -> None:
        with self.assertRaises(EspoDbError):
            EspoDb(host="", dbname="", user="")


class MergeTests(unittest.TestCase):
    def test_keeps_highest_score(self) -> None:
        by_id: dict[str, DuplicateCandidate] = {}
        EspoDb._merge(by_id, DuplicateCandidate("Contact", "1", "Jane", 0.5, "name"))
        EspoDb._merge(by_id, DuplicateCandidate("Contact", "1", "Jane", 1.0, "email"))
        EspoDb._merge(by_id, DuplicateCandidate("Contact", "1", "Jane", 0.7, "name"))
        self.assertEqual(by_id["1"].score, 1.0)
        self.assertEqual(by_id["1"].matched_on, "email")

    def test_distinct_ids_coexist(self) -> None:
        by_id: dict[str, DuplicateCandidate] = {}
        EspoDb._merge(by_id, DuplicateCandidate("Account", "1", "Acme", 0.6, "name"))
        EspoDb._merge(by_id, DuplicateCandidate("Account", "2", "Acme LLC", 0.5, "name"))
        self.assertEqual(len(by_id), 2)


class SchemaConstantTests(unittest.TestCase):
    def test_required_schema_shape(self) -> None:
        # Every entry maps a non-empty table name to a non-empty column tuple,
        # and the email/phone join tables are present (the matching depends on them).
        for table, cols in REQUIRED_SCHEMA.items():
            self.assertTrue(table)
            self.assertTrue(cols)
        for join in ("entity_email_address", "entity_phone_number", "email_address"):
            self.assertIn(join, REQUIRED_SCHEMA)


class HealthFormatTests(unittest.TestCase):
    def test_ok_health_has_no_missing_line(self) -> None:
        h = DbHealth(ok=True, connected=True, read_only=True, pg_trgm=True)
        text = "\n".join(h.format_lines())
        self.assertNotIn("missing", text)

    def test_missing_schema_is_reported(self) -> None:
        h = DbHealth(
            ok=False, connected=True, read_only=True, pg_trgm=True,
            missing_schema=["account.fein"],
        )
        text = "\n".join(h.format_lines())
        self.assertIn("account.fein", text)


if __name__ == "__main__":
    unittest.main()
