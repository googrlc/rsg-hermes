"""Staleness assertions — does the pipeline still move?

Every silent failure found on 2026-07-26 looked exactly like idle: the KPI job
404ing for months, ops-doctor permanently red, 18 tasks never closing, the
writeback failing once on 07-20 with nobody the wiser. A reachability check sees
none of it, because `select 1` succeeds against a pipeline that died in April.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hermes.operations import staleness as S

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def rule(**kw):
    base = dict(name="test pipeline", table="t", column="created_at",
                max_age_days=7, so_what="it stopped")
    base.update(kw)
    return S.StalenessRule(**base)


class FakeSupa:
    """Mimics the PostgREST behaviour the checks depend on."""

    def __init__(self, rows=None, error=None, columns=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.columns = columns
        self.calls: list[dict] = []

    def select(self, table, *, columns="*", params=None, limit=1000):
        self.calls.append({"table": table, "columns": columns, "params": dict(params or {})})
        if self.error:
            raise RuntimeError(self.error)
        if self.columns is not None:
            return [{c: "x" for c in self.columns}][:limit]
        return list(self.rows)[:limit]


def ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


# --- the two distinct failures ----------------------------------------------

def test_recent_activity_is_ok():
    c = S.check_rule(FakeSupa([{"created_at": ts(1)}]), rule(), now=NOW)
    assert c.verdict == S.VERDICT_OK and c.ok
    assert c.age_days == pytest.approx(1.0, abs=0.01)


def test_old_activity_is_stale():
    c = S.check_rule(FakeSupa([{"created_at": ts(30)}]), rule(max_age_days=7), now=NOW)
    assert c.verdict == S.VERDICT_STALE and not c.ok
    assert "limit 7d" in c.line


def test_no_rows_ever_is_never_not_stale():
    """'Never happened' and 'stopped happening' need different responses — one is
    not-wired, the other is something that died."""
    c = S.check_rule(FakeSupa([]), rule(), now=NOW)
    assert c.verdict == S.VERDICT_NEVER and not c.ok
    assert "no rows, ever" in c.line


def test_never_ok_lets_a_pipeline_be_legitimately_unfired():
    c = S.check_rule(FakeSupa([]), rule(never_ok=True), now=NOW)
    assert c.verdict == S.VERDICT_OK


def test_exactly_at_the_threshold_is_still_ok():
    c = S.check_rule(FakeSupa([{"created_at": ts(7)}]), rule(max_age_days=7), now=NOW)
    assert c.verdict == S.VERDICT_OK


def test_an_unreadable_table_is_unknown_not_never():
    """A broken check is a bug in the check. Reporting it as 'never moved' would
    send someone to fix the wrong thing."""
    c = S.check_rule(FakeSupa(error="permission denied"), rule(), now=NOW)
    assert c.verdict == S.VERDICT_UNKNOWN and not c.ok
    assert "permission denied" in c.line


def test_an_unparseable_timestamp_reads_as_never():
    c = S.check_rule(FakeSupa([{"created_at": "not-a-date"}]), rule(), now=NOW)
    assert c.verdict == S.VERDICT_NEVER


def test_a_naive_timestamp_is_treated_as_utc():
    naive = (NOW - timedelta(days=2)).replace(tzinfo=None).isoformat()
    c = S.check_rule(FakeSupa([{"created_at": naive}]), rule(), now=NOW)
    assert c.verdict == S.VERDICT_OK


def test_a_z_suffixed_timestamp_parses():
    z = (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    c = S.check_rule(FakeSupa([{"created_at": z}]), rule(), now=NOW)
    assert c.verdict == S.VERDICT_OK


# --- query shape ------------------------------------------------------------

def test_nulls_are_excluded_and_sorted_last():
    """PostgREST sorts nulls FIRST on a descending order, which would hand back a
    null row and read as 'never' on a table that is moving fine."""
    supa = FakeSupa([{"created_at": ts(1)}])
    S.check_rule(supa, rule(), now=NOW)
    params = supa.calls[0]["params"]
    assert params["order"] == "created_at.desc.nullslast"
    assert params["created_at"] == "not.is.null"


def test_rule_filters_are_passed_through():
    supa = FakeSupa([{"updated_at": ts(1)}])
    S.check_rule(supa, rule(column="updated_at",
                            filters={"status": "eq.completed"}), now=NOW)
    assert supa.calls[0]["params"]["status"] == "eq.completed"


def test_a_filter_never_overrides_the_null_guard():
    supa = FakeSupa([{"created_at": ts(1)}])
    S.check_rule(supa, rule(filters={"created_at": "gte.2020-01-01"}), now=NOW)
    # setdefault must not clobber an explicit filter on the same column.
    assert supa.calls[0]["params"]["created_at"] == "gte.2020-01-01"


# --- the monitor monitors itself --------------------------------------------

def test_validate_rules_catches_a_column_that_does_not_exist():
    """The bug this guards actually happened while writing the rules:
    dashboard_kpis.created_at does not exist, the column is recorded_at. The check
    degraded to UNKNOWN forever, which looks like missing data rather than a typo."""
    supa = FakeSupa(columns=["recorded_at"])
    problems = S.validate_rules(supa, (rule(table="dashboard_kpis", column="created_at"),))
    assert problems and "no column 'created_at'" in problems[0]


def test_validate_rules_passes_when_the_column_exists():
    assert S.validate_rules(FakeSupa(columns=["created_at"]), (rule(),)) == []


def test_validate_rules_reports_an_unreadable_table():
    problems = S.validate_rules(FakeSupa(error="relation does not exist"), (rule(),))
    assert problems and "cannot read" in problems[0]


def test_validate_rules_tolerates_an_empty_table():
    """No rows is not proof the column is wrong."""
    assert S.validate_rules(FakeSupa([]), (rule(),)) == []


# --- the shipped rule set ---------------------------------------------------

def test_every_shipped_rule_explains_what_the_silence_means():
    """A red light with no explanation becomes furniture — the exact failure this
    module exists to fix."""
    for r in S.RULES:
        assert len(r.so_what) > 40, r.name
        assert r.max_age_days > 0


def test_shipped_rules_have_unique_names():
    names = [r.name for r in S.RULES]
    assert len(names) == len(set(names))


def test_the_writeback_rule_treats_never_as_a_failure():
    """It has never completed once. That must not read as OK."""
    r = next(x for x in S.RULES if "writeback" in x.name)
    assert r.never_ok is False
    assert r.filters.get("status") == "eq.completed"


# --- report -----------------------------------------------------------------

def test_report_is_not_ok_when_anything_stopped():
    rpt = S.StalenessReport(checks=[
        S.StalenessCheck(rule(), S.VERDICT_OK, age_days=1.0),
        S.StalenessCheck(rule(name="dead one"), S.VERDICT_NEVER),
    ])
    assert not rpt.ok and len(rpt.problems) == 1


def test_report_explains_only_the_problems():
    rpt = S.StalenessReport(checks=[
        S.StalenessCheck(rule(name="fine", so_what="A" * 50), S.VERDICT_OK, age_days=1.0),
        S.StalenessCheck(rule(name="broken", so_what="B" * 50), S.VERDICT_STALE, age_days=99.0),
    ])
    text = "\n".join(rpt.format_lines())
    assert "What the silence means" in text
    assert "B" * 50 in text and "A" * 50 not in text


def test_an_all_ok_report_says_nothing_about_meaning():
    rpt = S.StalenessReport(checks=[S.StalenessCheck(rule(), S.VERDICT_OK, age_days=0.5)])
    assert rpt.ok
    assert "What the silence means" not in "\n".join(rpt.format_lines())


def test_check_staleness_never_raises_on_a_broken_database():
    rpt = S.check_staleness(FakeSupa(error="connection refused"))
    assert not rpt.ok
    assert all(c.verdict == S.VERDICT_UNKNOWN for c in rpt.checks)
    assert len(rpt.checks) == len(S.RULES)
