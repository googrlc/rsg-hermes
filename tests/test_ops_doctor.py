"""Reachability probe assertions for ``hermes --ops-doctor``.

The probe counts rows by selecting a single column. That is only safe if the
column exists: PostgREST answers an unknown column with a 400, which ops-doctor
records as a table failure. #248 added canonical_clients / canonical_policies to
HERMES_TABLES, but those two are keyed by NowCerts guids and have no surrogate
`id`, so every run reported ISSUES FOUND with two errors that had nothing to do
with the agency's data.

A health check that is permanently red is a health check people stop reading —
the exact trap the HERMES_TABLES comment warns about.
"""

from __future__ import annotations

import pytest

from hermes_integrations.supabase_client import SupabaseClientError
from hermes.operations import ops_doctor as OD

# The real key columns, as they exist in Supabase. canonical_* are keyed by the
# NowCerts guids; everything else carries a surrogate id.
REAL_COLUMNS = {
    "canonical_clients": {"nowcerts_insured_guid", "insured_name", "updated_at"},
    "canonical_policies": {"policy_guid", "policy_number", "created_at"},
}

EXPECTED_ROLES = (
    "HermesCommissionAuditor",
    "HermesRenewalSpecialist",
    "HermesFinanceOps",
    "HermesOpsRouter",
)


class SchemaAwareSupa:
    """Mimics PostgREST rejecting a select of a column the table lacks."""

    def __init__(self, columns_by_table=None):
        self.columns_by_table = columns_by_table or REAL_COLUMNS
        self.probed: dict[str, str] = {}

    def select(self, table, *, columns="*", params=None, limit=100):
        known = self.columns_by_table.get(table)
        if known is not None and columns not in known and columns != "*":
            self.probed[table] = columns
            raise SupabaseClientError(
                f'SELECT {table}: {{"code":"42703","message":"column '
                f'{table}.{columns} does not exist"}}'
            )
        # The AI-roles check is a separate concern; keep it satisfied so it
        # cannot masquerade as a reachability failure.
        if table == "hermes_ai_roles":
            return [{"role_name": r} for r in EXPECTED_ROLES]
        self.probed[table] = columns
        return [{columns: "x"}]


def test_canonical_tables_are_probed_by_their_guid_key():
    """The regression: probing `id` 400s on the canonical book."""
    supa = SchemaAwareSupa()

    report = OD.run_ops_doctor(supa, check_movement=False, check_llm=False)

    assert supa.probed["canonical_clients"] == "nowcerts_insured_guid"
    assert supa.probed["canonical_policies"] == "policy_guid"
    assert report.errors == []
    assert report.ok


def test_tables_without_an_override_still_probe_id():
    supa = SchemaAwareSupa()

    OD.run_ops_doctor(supa, check_movement=False, check_llm=False)

    assert supa.probed["outbound_sync_queue"] == "id"
    assert supa.probed["commission_ledger"] == "id"


def test_every_probed_table_has_a_key_column_declared():
    """Guards the next table someone appends to HERMES_TABLES."""
    for table in OD.HERMES_TABLES:
        key = OD.TABLE_KEY_COLUMNS.get(table, OD.DEFAULT_KEY_COLUMN)
        assert key, f"{table} resolves to an empty probe column"


@pytest.mark.parametrize("table", ["canonical_clients", "canonical_policies"])
def test_a_bad_probe_column_is_still_reported_as_a_failure(table):
    """The error path stays intact — this is a fix, not a silencer."""
    supa = SchemaAwareSupa({table: {"some_other_column"}})

    report = OD.run_ops_doctor(supa, check_movement=False, check_llm=False)

    assert not report.ok
    assert any(table in e and "does not exist" in e for e in report.errors)
