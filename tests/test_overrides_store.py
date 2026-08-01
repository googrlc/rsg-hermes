"""Tests for override persistence and the sync-time reconcile pass.

The audit log is the record; the override table is only current state. Every
mutation must log, and reconcile must never silently discard a correction.
"""

from __future__ import annotations

import pytest

from hermes_core.overrides import core, store

E = "commission_ledger"


class FakeSupa:
    """Minimal PostgREST-ish fake: eq. filters, insert, update."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self._seq = 0

    def select(self, table, *, columns="*", params=None, limit=1000):
        rows = self.tables.get(table, [])
        for key, val in (params or {}).items():
            if key == "order":
                continue
            if isinstance(val, str) and val.startswith("eq."):
                rows = [r for r in rows if str(r.get(key)) == val[3:]]
        return [dict(r) for r in rows][:limit]

    def insert(self, table, payload):
        self._seq += 1
        row = {"id": f"id-{self._seq}", **payload}
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def update(self, table, record_id, payload):
        for row in self.tables.get(table, []):
            if row.get("id") == record_id:
                row.update(payload)
                return dict(row)
        raise AssertionError(f"no row {record_id} in {table}")


@pytest.fixture
def supa():
    return FakeSupa()


def logs(supa):
    return supa.tables.get(store.WRITE_LOG_TABLE, [])


def overrides(supa):
    return supa.tables.get(store.OVERRIDES_TABLE, [])


# --- set ---------------------------------------------------------------------

def test_set_creates_an_active_override_and_logs_it(supa):
    store.set_override(
        supa, entity_type=E, entity_key="P1", field_name="gross_premium",
        override_value=1000, original_value=0,
        approved_by="lamar@risksolutionsgroup.net", reason="AMS missing premium",
    )
    (row,) = overrides(supa)
    assert row["status"] == core.STATUS_ACTIVE
    assert row["override_value"] == 1000
    assert row["original_value"] == 0

    (entry,) = logs(supa)
    assert entry["action"] == store.ACT_SET
    assert entry["before_value"] == 0 and entry["after_value"] == 1000
    assert entry["actor"] == "lamar@risksolutionsgroup.net"


def test_setting_again_supersedes_rather_than_duplicating(supa):
    kw = dict(entity_type=E, entity_key="P1", field_name="gross_premium",
              approved_by="lamar@risksolutionsgroup.net")
    store.set_override(supa, override_value=1000, original_value=0, **kw)
    store.set_override(supa, override_value=1200, original_value=0, **kw)

    rows = overrides(supa)
    assert len(rows) == 2
    active = [r for r in rows if r["status"] == core.STATUS_ACTIVE]
    assert len(active) == 1 and active[0]["override_value"] == 1200
    retired = [r for r in rows if r["status"] == core.STATUS_RETIRED]
    assert retired[0]["retired_reason"] == store.REASON_SUPERSEDED
    assert any(e["action"] == store.ACT_SUPERSEDED for e in logs(supa))


def test_approved_by_is_required(supa):
    with pytest.raises(ValueError, match="approved_by"):
        store.set_override(
            supa, entity_type=E, entity_key="P1", field_name="gross_premium",
            override_value=1, original_value=0, approved_by="",
        )


def test_entity_key_is_required(supa):
    with pytest.raises(ValueError):
        store.set_override(
            supa, entity_type=E, entity_key="  ", field_name="x",
            override_value=1, original_value=0, approved_by="a@b.net",
        )


# --- withdraw ----------------------------------------------------------------

def test_withdraw_retires_and_logs(supa):
    row = store.set_override(
        supa, entity_type=E, entity_key="P1", field_name="gross_premium",
        override_value=1000, original_value=0, approved_by="a@b.net",
    )
    store.withdraw(supa, row["id"], actor="a@b.net", note="wrong number")
    assert overrides(supa)[0]["status"] == core.STATUS_RETIRED
    assert overrides(supa)[0]["retired_reason"] == store.REASON_WITHDRAWN
    assert any(e["action"] == store.ACT_WITHDRAWN for e in logs(supa))


def test_withdraw_unknown_id_raises(supa):
    with pytest.raises(ValueError, match="not found"):
        store.withdraw(supa, "nope", actor="a@b.net")


# --- reconcile ---------------------------------------------------------------

def _seed(supa, *, override_value, original_value):
    return store.set_override(
        supa, entity_type=E, entity_key="P1", field_name="gross_premium",
        override_value=override_value, original_value=original_value,
        approved_by="a@b.net",
    )


def test_reconcile_retires_when_the_ams_catches_up(supa):
    _seed(supa, override_value=1000, original_value=0)
    out = store.reconcile_overrides(supa, E, {("P1", "gross_premium"): 1000})
    assert out["retired"] == 1 and out["conflicted"] == 0
    assert overrides(supa)[0]["status"] == core.STATUS_RETIRED
    assert overrides(supa)[0]["retired_reason"] == core.RETIRED_AMS_MATCHED
    assert any(e["action"] == store.ACT_RETIRED for e in logs(supa))


def test_reconcile_keeps_when_the_source_is_unchanged(supa):
    _seed(supa, override_value=1000, original_value=0)
    out = store.reconcile_overrides(supa, E, {("P1", "gross_premium"): 0})
    assert out["kept"] == 1 and out["retired"] == 0
    assert overrides(supa)[0]["status"] == core.STATUS_ACTIVE


def test_reconcile_flags_a_conflict_instead_of_discarding_the_correction(supa):
    """The branch that protects the human's work."""
    _seed(supa, override_value=1000, original_value=0)
    out = store.reconcile_overrides(supa, E, {("P1", "gross_premium"): 750})
    assert out["conflicted"] == 1 and out["retired"] == 0
    row = overrides(supa)[0]
    assert row["status"] == core.STATUS_CONFLICTED
    assert row["conflict_value"] == 750
    assert row["override_value"] == 1000        # the correction is NOT lost
    assert any(e["action"] == store.ACT_CONFLICTED for e in logs(supa))


def test_a_field_absent_from_the_source_is_left_alone(supa):
    """The source not reporting a field is not evidence the override is stale."""
    _seed(supa, override_value=1000, original_value=0)
    out = store.reconcile_overrides(supa, E, {})
    assert out["kept"] == 1 and out["checked"] == 0
    assert overrides(supa)[0]["status"] == core.STATUS_ACTIVE


def test_dry_run_reports_without_writing(supa):
    _seed(supa, override_value=1000, original_value=0)
    out = store.reconcile_overrides(
        supa, E, {("P1", "gross_premium"): 1000}, dry_run=True,
    )
    assert out["retired"] == 1 and out["dry_run"] is True
    assert overrides(supa)[0]["status"] == core.STATUS_ACTIVE   # untouched
    assert not any(e["action"] == store.ACT_RETIRED for e in logs(supa))


def test_reconcile_tolerates_type_drift_from_the_source(supa):
    _seed(supa, override_value=1000.0, original_value=0)
    out = store.reconcile_overrides(supa, E, {("P1", "gross_premium"): "1000.00"})
    assert out["retired"] == 1


def test_only_this_entity_types_overrides_are_touched(supa):
    _seed(supa, override_value=1000, original_value=0)
    out = store.reconcile_overrides(
        supa, "canonical_policies", {("P1", "gross_premium"): 1000},
    )
    assert out["retired"] == 0
    assert overrides(supa)[0]["status"] == core.STATUS_ACTIVE


def test_active_overrides_indexes_by_key_and_field(supa):
    _seed(supa, override_value=1000, original_value=0)
    idx = store.active_overrides(supa, E)
    assert (E, "P1", "gross_premium") in idx
    assert idx[(E, "P1", "gross_premium")].override_value == 1000
