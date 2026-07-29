"""Merging the duplicate deals the quote sync left on the pipeline."""

from __future__ import annotations

from typing import Any

from hermes.intake import opportunities as opp
from hermes.sync import opportunity_dedupe as dd


class FakeSupa:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = [dict(r) for r in rows]
        self.deleted: list[str] = []
        self.updates: list[tuple[str, dict]] = []

    def select(self, table, *, columns="*", params=None, limit=100):
        return [dict(r) for r in self.rows][:limit]

    def update(self, table, record_id, payload):
        self.updates.append((record_id, dict(payload)))
        for r in self.rows:
            if str(r.get("id")) == str(record_id):
                r.update(payload)
                return dict(r)
        raise AssertionError("no such row")

    def delete(self, table, record_id):
        self.deleted.append(str(record_id))
        self.rows = [r for r in self.rows if str(r.get("id")) != str(record_id)]


def pair_rows(**over):
    keep = {"id": "keep-1", "client_identifier": "craig-p-courtney",
            "line_of_business": "Personal Auto", "opportunity_type": "Renewals",
            "stage": "Quotes Received", "premium_estimate": 1170, "sync_source": "crm"}
    drop = {"id": "drop-1", "client_identifier": "craig-p-courtney",
            "line_of_business": "Personal Auto", "opportunity_type": "New Business",
            "stage": "Quotes Received", "premium_estimate": 1170,
            "sync_source": "nowcerts_quote_sync",
            "quote_number": "Q-77", "nowcerts_quote_guid": "qg-77", "carrier": "Progressive"}
    keep.update(over.pop("keep", {}))
    drop.update(over.pop("drop", {}))
    return [keep, drop]


def test_it_finds_the_pair_and_keeps_the_worked_row():
    res = dd.run_dedupe(FakeSupa(pair_rows()))
    assert len(res.pairs) == 1
    p = res.pairs[0]
    assert p.keep["id"] == "keep-1"          # the human's row survives
    assert p.drop["id"] == "drop-1"          # the sync's twin goes


def test_nothing_is_written_without_apply():
    supa = FakeSupa(pair_rows())
    res = dd.run_dedupe(supa)
    assert supa.deleted == [] and supa.updates == []
    assert res.merged == 0 and res.deleted == 0


def test_apply_carries_the_identifiers_across_before_deleting():
    """The sync row is not worthless — it holds the NowCerts identifiers the CRM
    row never had. Deleting it without carrying those over loses the link to the
    AMS."""
    supa = FakeSupa(pair_rows())
    res = dd.run_dedupe(supa, apply=True)
    assert res.merged == 1 and res.deleted == 1
    rec_id, payload = supa.updates[0]
    assert rec_id == "keep-1"
    assert payload["quote_number"] == "Q-77"
    assert payload["nowcerts_quote_guid"] == "qg-77"
    assert supa.deleted == ["drop-1"]


def test_it_never_overwrites_a_value_the_survivor_already_has():
    supa = FakeSupa(pair_rows(keep={"carrier": "Safeco"}))
    dd.run_dedupe(supa, apply=True)
    payload = supa.updates[0][1]
    assert "carrier" not in payload          # the CRM's carrier stands


def test_differing_premiums_are_left_alone():
    """Same client and LOB but a different premium may be a genuinely new quote,
    not a duplicate. Reported, not merged."""
    supa = FakeSupa(pair_rows(drop={"premium_estimate": 2400}))
    res = dd.run_dedupe(supa, apply=True)
    assert res.deleted == 0 and supa.deleted == []
    assert len(res.skipped) == 1 and "premiums differ" in res.skipped[0]


def test_two_human_rows_are_never_touched():
    """Two rows nobody synced is a decision someone made. Not ours to undo."""
    rows = pair_rows()
    rows[1]["sync_source"] = "crm"
    res = dd.run_dedupe(FakeSupa(rows), apply=True)
    assert res.pairs == [] and res.deleted == 0


def test_three_rows_on_one_client_and_lob_are_left_for_a_human():
    rows = pair_rows()
    rows.append({**rows[0], "id": "third", "opportunity_type": "Cross-selling"})
    res = dd.run_dedupe(FakeSupa(rows), apply=True)
    assert res.pairs == [] and res.deleted == 0


def test_a_failure_on_one_pair_does_not_stop_the_sweep():
    class Boom(FakeSupa):
        def delete(self, table, record_id):
            if record_id == "drop-1":
                raise RuntimeError("row locked")
            super().delete(table, record_id)

    rows = pair_rows() + [
        {"id": "keep-2", "client_identifier": "acme", "line_of_business": "GL",
         "opportunity_type": "Renewals", "premium_estimate": 500, "sync_source": "crm"},
        {"id": "drop-2", "client_identifier": "acme", "line_of_business": "GL",
         "opportunity_type": "New Business", "premium_estimate": 500,
         "sync_source": "nowcerts_quote_sync"},
    ]
    res = dd.run_dedupe(Boom(rows), apply=True)
    assert res.deleted == 1 and len(res.errors) == 1
    assert "row locked" in res.errors[0]


def test_the_survivor_keeps_the_type_that_makes_the_board_honest():
    """The whole point: the pipeline stops counting a renewal as new business."""
    supa = FakeSupa(pair_rows())
    dd.run_dedupe(supa, apply=True)
    assert len(supa.rows) == 1
    assert supa.rows[0]["opportunity_type"] == "Renewals"
    assert supa.rows[0]["opportunity_type"] != opp.TYPE_NEW_BUSINESS
