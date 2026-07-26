"""One writer per row on the canonical book.

canonical_policies had two writers and no way to say which owned a row.
`rsg-import` (pg_cron) pulled only is_quote=false and tombstoned everything absent
from that pull — including rows it never created. 43 of 48 tombstoned rows belong
to the csv-import load, against 5 of its own. It was disabled 2026-07-24.

The rule sync_owner encodes: ANY writer may refresh volatile fields on any row;
only the OWNER may deactivate or tombstone one.
"""

from __future__ import annotations

import pytest

from hermes.ams import book as B


# --- the guard ---------------------------------------------------------------

def test_a_writer_may_retire_its_own_row():
    assert B.may_deactivate({"sync_owner": "rsg-import"}, B.OWNER_RSG_IMPORT)


def test_a_writer_may_not_retire_someone_elses_row():
    """This is the exact write that corrupted 43 rows in July."""
    assert not B.may_deactivate({"sync_owner": "csv-import"}, B.OWNER_RSG_IMPORT)
    assert not B.may_deactivate({"sync_owner": "book_sync"}, B.OWNER_RSG_IMPORT)


def test_an_unowned_row_is_claimable():
    """Legacy rows predate the column. Refusing here would freeze them forever."""
    assert B.may_deactivate({"sync_owner": None}, B.OWNER_RSG_IMPORT)
    assert B.may_deactivate({}, B.OWNER_BOOK_SYNC)


def test_whitespace_owner_is_treated_as_unowned():
    assert B.may_deactivate({"sync_owner": "   "}, B.OWNER_BOOK_SYNC)


def test_book_sync_may_not_retire_an_rsg_import_row_either():
    """The rule is symmetric — it is not 'rsg-import is the bad one'."""
    assert not B.may_deactivate({"sync_owner": "rsg-import"}, B.OWNER_BOOK_SYNC)


# --- the tombstone predicate, now single-homed -------------------------------

def test_tombstoned_rows_are_recognised():
    assert B.is_tombstoned({"status": "Inactive: not in NowCerts (checked 2026-07-20)"})
    assert B.is_tombstoned({"status": B.TOMBSTONE_PREFIX})


@pytest.mark.parametrize("status", ["Active", "", None, "Cancelled", "Inactive"])
def test_real_statuses_are_not_tombstones(status):
    assert not B.is_tombstoned({"status": status})


def test_all_three_call_sites_agree():
    """The check used to exist as two independent copies of a magic string, so a
    consumer that forgot it counted phantom rows as real book."""
    from hermes.commissions.surface import _is_tombstoned as surface_check
    from hermes.jobs.agency_snapshot import is_tombstoned as snapshot_check

    row = {"status": "Inactive: not in NowCerts (checked 2026-07-20)"}
    assert B.is_tombstoned(row) is snapshot_check(row) is surface_check(row) is True

    live = {"status": "Active"}
    assert B.is_tombstoned(live) is snapshot_check(live) is surface_check(live) is False


def test_the_prefix_has_one_definition():
    from hermes.commissions.surface import TOMBSTONE_PREFIX as surface_prefix
    from hermes.jobs.agency_snapshot import TOMBSTONE_PREFIX as snapshot_prefix

    assert B.TOMBSTONE_PREFIX == snapshot_prefix == surface_prefix


# --- the writer claims what it creates --------------------------------------

def test_owner_constants_do_not_drift():
    """book.py and canonical_book_sync.py each define it — book imports FROM sync,
    so the dependency can only run one way."""
    from hermes.sync.canonical_book_sync import OWNER_BOOK_SYNC

    assert OWNER_BOOK_SYNC == B.OWNER_BOOK_SYNC == "book_sync"


def test_a_created_policy_claims_ownership():
    from hermes.sync import canonical_book_sync as CBS

    inserted: list[dict] = []

    class FakeSupa:
        def select(self, table, *, columns="*", params=None, limit=1000):
            # _discover_columns reads one row to learn the live column set.
            return [{"policy_guid": "existing", "renewed_policy": None,
                     "sync_owner": None, "policy_number": "P0", "active": True}]

        def insert(self, table, payload):
            inserted.append(payload)
            return {"id": "new", **payload}

        def update_where(self, table, payload, *, filters):
            return [{"id": "u"}]

    result = CBS.CanonicalSyncResult()
    CBS._sync_policies(
        FakeSupa(),
        [{"databaseId": "brand-new-guid", "policyNumber": "P9", "active": True}],
        dry_run=False,
        result=result,
    )
    assert result.policies_created == 1
    assert inserted and inserted[0].get("sync_owner") == B.OWNER_BOOK_SYNC


def test_an_existing_policy_is_refreshed_without_reclaiming_it():
    """Refreshing volatile fields must not steal ownership — that would let the
    last writer to touch a row acquire the right to kill it."""
    from hermes.sync import canonical_book_sync as CBS

    updates: list[dict] = []

    class FakeSupa:
        def select(self, table, *, columns="*", params=None, limit=1000):
            return [{"policy_guid": "known-guid", "renewed_policy": None,
                     "sync_owner": "csv-import", "policy_number": "P0", "active": True}]

        def insert(self, table, payload):
            raise AssertionError("should have updated, not inserted")

        def update_where(self, table, payload, *, filters):
            updates.append(payload)
            return [{"id": "u"}]

    result = CBS.CanonicalSyncResult()
    CBS._sync_policies(
        FakeSupa(),
        [{"databaseId": "known-guid", "policyNumber": "P0", "active": True}],
        dry_run=False,
        result=result,
    )
    assert result.policies_updated == 1
    assert all("sync_owner" not in p for p in updates)
