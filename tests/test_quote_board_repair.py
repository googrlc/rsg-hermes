"""Repairing the board from the AMS quote register."""

from __future__ import annotations

from typing import Any

from hermes.intake import opportunities as opp
from hermes.sync import quote_board_repair as R


class FakeSupa:
    def __init__(self, opps, quotes):
        self.data = {opp.TABLE: [dict(o) for o in opps], R.QUOTES_TABLE: [dict(q) for q in quotes]}
        self.updates: list[tuple[str, dict]] = []

    def select(self, table, *, columns="*", params=None, limit=100):
        return [dict(r) for r in self.data.get(table, [])][:limit]

    def update(self, table, record_id, payload):
        self.updates.append((record_id, dict(payload)))
        for r in self.data[table]:
            if str(r.get("id")) == str(record_id):
                r.update(payload)
                return dict(r)
        raise AssertionError("no such row")


def board(**over):
    row = {"id": "o1", "insured_name": "Huff, Phyllis", "line_of_business": "Personal Auto",
           "opportunity_type": "New Business", "stage": "Quotes Received",
           "premium_estimate": 2377.0, "effective_date": "2025-04-18",
           "nowcerts_quote_guid": "qg-1", "sync_source": "nowcerts_quote_sync",
           "assigned_to_email": None, "expected_close_date": None}
    row.update(over)
    return row


def register(**over):
    q = {"nowcerts_quote_guid": "qg-1", "business_type": "Renewal", "premium_estimate": 2253,
         "carrier": "Progressive", "line_of_business": "Personal Auto",
         "effective_date": "2025-04-18", "expiration_date": "2025-10-18"}
    q.update(over)
    return q


def test_it_corrects_the_type_the_sync_guessed():
    res = R.plan(FakeSupa([board()], [register()]))
    assert res.fixes[0].changes["opportunity_type"] == ("New Business", "Renewals")


def test_correcting_the_type_also_moves_the_stage_to_that_ladder():
    """Renewals run a different ladder — 'Quotes Received' does not exist on it,
    and writing the type without the stage makes the row unsavable."""
    res = R.plan(FakeSupa([board()], [register()]))
    old, new = res.fixes[0].changes["stage"]
    assert new in opp.stages_for_type("Renewals")


def test_it_restores_the_premium_that_belongs_with_the_dates():
    res = R.plan(FakeSupa([board()], [register()]))
    assert res.fixes[0].changes["premium_estimate"] == (2377.0, 2253.0)


def test_a_human_worked_row_keeps_its_type():
    """A deal somebody typed in the CRM is a decision, not a sync guess."""
    res = R.plan(FakeSupa([board(sync_source="crm")], [register()]))
    assert "opportunity_type" not in res.fixes[0].changes


def test_owners_go_to_the_column_the_crm_reads():
    res = R.plan(FakeSupa([board()], [register()]))
    changes = res.fixes[0].changes
    assert changes["assigned_to_email"][1] == R._owner_for("Personal Auto")
    assert "assigned_to" not in changes


def test_commercial_lines_route_to_lamar():
    res = R.plan(FakeSupa([board(line_of_business="Commercial Auto")], [register(line_of_business="Commercial Auto")]))
    assert res.fixes[0].changes["assigned_to_email"][1].startswith("lamar@")


def test_the_close_date_is_set_from_the_effective_date():
    res = R.plan(FakeSupa([board()], [register()]))
    assert res.fixes[0].changes["expected_close_date"][1] == "2025-04-18"


def test_a_row_with_no_matching_quote_is_reported_not_guessed():
    res = R.plan(FakeSupa([board(nowcerts_quote_guid="qg-missing")], [register()]))
    assert res.unmatched and "Huff" in res.unmatched[0]


def test_nothing_is_written_without_apply():
    supa = FakeSupa([board()], [register()])
    res = R.run_repair(supa)
    assert supa.updates == [] and res.applied == 0 and res.backup_path is None


def test_apply_backs_up_first(tmp_path):
    supa = FakeSupa([board()], [register()])
    res = R.run_repair(supa, apply=True, backup_dir=str(tmp_path))
    assert res.backup_path and res.applied == 1
    import json
    saved = json.loads(open(res.backup_path).read())
    assert saved[0]["premium_estimate"] == 2377.0     # the value BEFORE the fix


def test_one_bad_row_does_not_stop_the_sweep(tmp_path):
    class Boom(FakeSupa):
        def update(self, table, record_id, payload):
            if record_id == "o1":
                raise RuntimeError("locked")
            return super().update(table, record_id, payload)

    supa = Boom([board(), board(id="o2", nowcerts_quote_guid="qg-2")],
                [register(), register(nowcerts_quote_guid="qg-2")])
    res = R.run_repair(supa, apply=True, backup_dir=str(tmp_path))
    assert res.applied == 1 and len(res.errors) == 1


def test_a_clean_row_needs_no_work():
    clean = board(opportunity_type="Renewals", stage="Requote Renewal", premium_estimate=2253,
                  assigned_to_email="gretchen@risksolutionsgroup.net",
                  expected_close_date="2025-04-18", carrier="Progressive",
                  expiration_date="2025-10-18")
    res = R.plan(FakeSupa([clean], [register()]))
    assert res.fixes == []


class FakeNC:
    def __init__(self, quotes): self._q = quotes
    def fetch_policies(self, **kw): return list(self._q)


def live_quote(**over):
    q = {"isQuote": True, "databaseId": "qg-1", "businessType": "Renewal",
         "totalPremium": 2253, "carrierName": "Progressive",
         "lineOfBusinesses": [{"lineOfBusinessName": "Personal Auto"}],
         "effectiveDate": "2025-04-18", "expirationDate": "2025-10-18"}
    q.update(over)
    return q


def test_the_live_register_outranks_the_snapshot():
    """canonical_quotes was loaded once on 2026-07-21 and has not run since. A
    quote dispositioned in the AMS after that is still sitting in the snapshot
    looking authoritative, and repairing from it would restore what somebody
    deliberately retired."""
    stale = register(premium_estimate=9999, business_type="New Business")
    supa = FakeSupa([board()], [stale])
    res = R.plan(supa, FakeNC([live_quote()]))
    assert res.source == "nowcerts (live)"
    assert res.fixes[0].changes["premium_estimate"][1] == 2253.0     # live, not 9999


def test_a_quote_the_ams_no_longer_has_is_reported_not_silently_kept():
    """After a purge, the board row is the leftover. It surfaces as unmatched so
    a human decides, rather than being repaired against nothing."""
    supa = FakeSupa([board()], [register()])
    res = R.plan(supa, FakeNC([]))
    assert res.unmatched and "Huff" in res.unmatched[0]


def test_an_unreachable_ams_falls_back_to_the_snapshot_and_says_so():
    class Dead:
        def fetch_policies(self, **kw): raise RuntimeError("NowCerts down")

    res = R.plan(FakeSupa([board()], [register()]), Dead())
    assert res.source == "canonical_quotes"
    assert res.fixes                      # still does the work, from the snapshot


def test_a_board_row_whose_quote_is_no_longer_open_is_reported():
    """Only active + Received quotes belong on the board. A row whose quote has
    since been bound, declined or expired is reported so a human can retire it —
    this tool corrects values, it does not remove somebody's work."""
    supa = FakeSupa([board()], [register()])
    res = R.plan(supa, FakeNC([live_quote(quoteStageName="Bound")]))
    assert res.closed and "Huff" in res.closed[0]
    assert supa.updates == []


def test_an_open_quote_is_not_reported_as_closed():
    supa = FakeSupa([board()], [register()])
    res = R.plan(supa, FakeNC([live_quote(active=True, quoteStageName="Received")]))
    assert res.closed == []
