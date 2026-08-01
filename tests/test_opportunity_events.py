"""A deal's timeline — the pipeline's missing history."""
from __future__ import annotations

from hermes_core import opportunities as opp


class FakeSupa:
    def __init__(self, rows=None, *, insert_boom=False):
        self.rows = rows or {}
        self.insert_boom = insert_boom
        self.inserted: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, str, dict]] = []
        self.selects: list[tuple[str, dict]] = []

    def select(self, table, *, columns=None, params=None, limit=None):
        self.selects.append((table, params or {}))
        return list(self.rows.get(table, []))

    def insert(self, table, payload):
        if self.insert_boom:
            raise RuntimeError("supabase down")
        self.inserted.append((table, payload))
        return {"id": "ev-1", **payload}

    def update(self, table, row_id, payload):
        self.updated.append((table, row_id, payload))
        return {"id": row_id, **payload}


def _events(supa):
    return [p for t, p in supa.inserted if t == "opportunity_events"]


def test_a_stage_move_records_where_it_came_from():
    """'Moved to Lost' is half a fact — the row is about to forget the rest."""
    supa = FakeSupa({"opportunities": [{"stage": "Sent Proposal"}]})
    opp.advance_stage(supa, "opp-1", "Lost", moved_by="lamar@risksolutionsgroup.net")
    ev = _events(supa)[0]
    assert ev["event_type"] == "stage"
    assert ev["summary"] == "Moved from Sent Proposal to Lost"
    assert ev["details"] == {"from": "Sent Proposal", "to": "Lost",
                             "status": "lost", "lost_reason": None}
    assert ev["actor_email"] == "lamar@risksolutionsgroup.net"


def test_a_lost_reason_is_on_the_timeline_where_it_can_be_read():
    supa = FakeSupa({"opportunities": [{"stage": "Sent Proposal"}]})
    opp.advance_stage(supa, "opp-1", "Lost", lost_reason="went with the incumbent",
                      moved_by="gretchen@risksolutionsgroup.net")
    assert "went with the incumbent" in _events(supa)[0]["summary"]


def test_a_move_from_an_unknown_prior_stage_still_records():
    supa = FakeSupa()          # no prior row to read
    opp.advance_stage(supa, "opp-1", "Quotes Received")
    assert _events(supa)[0]["summary"] == "Moved to Quotes Received"


def test_the_stage_move_survives_a_timeline_write_failure():
    """Losing the audit line is bad; refusing the move because of it is worse."""
    supa = FakeSupa({"opportunities": [{"stage": "Preparing Application"}]}, insert_boom=True)
    row = opp.advance_stage(supa, "opp-1", "Sent For Quoting")
    assert row["stage"] == "Sent For Quoting"        # the move still happened


def test_log_event_returns_none_rather_than_raising():
    assert opp.log_event(FakeSupa(insert_boom=True), "opp-1", summary="x") is None


def test_the_timeline_reads_newest_first():
    supa = FakeSupa({"opportunity_events": [{"id": "ev-1"}]})
    opp.list_events(supa, "opp-1")
    table, params = supa.selects[0]
    assert table == "opportunity_events"
    assert params["order"] == "created_at.desc"
    assert params["opportunity_id"] == "eq.opp-1"


def test_a_note_and_a_stage_move_land_on_the_same_timeline():
    """One list answers 'what happened here' — no merging two by timestamp."""
    supa = FakeSupa({"opportunities": [{"stage": "Sent Proposal"}]})
    opp.log_event(supa, "opp-1", summary="Client wants to think about it")
    opp.advance_stage(supa, "opp-1", "Lost")
    kinds = [e["event_type"] for e in _events(supa)]
    assert kinds == ["note", "stage"]
    assert all(e["opportunity_id"] == "opp-1" for e in _events(supa))


def test_a_note_defaults_to_the_note_type():
    supa = FakeSupa()
    opp.log_event(supa, "opp-1", summary="Left a voicemail", actor_email="g@x.net")
    assert _events(supa)[0]["event_type"] == "note"


def test_moved_by_reaches_the_timeline_from_the_request_model():
    """The portal has always sent moved_by; the model dropped it, so every move
    was attributed to 'cockpit-stage-move'."""
    from hermes.api import StageUpdateRequest

    assert "moved_by" in StageUpdateRequest.model_fields
