"""The Command Center's intake queue — the non-Slack window onto intake_submissions.

This list used to be filtered to the two email lanes. With no Slack in the loop it
is the only place a person can see what the intake pipeline is doing, so a
submission from any other source being invisible here meant it was invisible
everywhere.
"""
from __future__ import annotations

from hermes.command_center import dashboard


class FakeSupa:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def select(self, table, *, columns=None, params=None, limit=None):
        self.params = params or {}
        source = (self.params.get("source") or "").removeprefix("in.(").rstrip(")")
        wanted = set(source.split(",")) if source else None
        return [r for r in self.rows if wanted is None or r.get("source") in wanted]


EMAIL_ROW = {
    "id": "e1", "source": "email-ms365", "status": "awaiting_approval",
    "payload": {"subject": "Renewal question", "from": "jo@client.example"},
}
GATE_ROW = {
    "id": "g1", "source": "intake_gate", "status": "awaiting_approval",
    "payload": {},
    "draft_summary": {
        "account": {"account_name": "Jarah Group LLC"},
        "opportunities": [
            {"line_of_business": "General Liability"},
            {"line_of_business": "Worker's Compensation"},
        ],
    },
}
UNSYNTHESIZED = {"id": "u1", "source": "cowork", "status": "received", "payload": {}}


def test_the_queue_shows_every_source_not_just_email():
    out = dashboard.intake_queue(FakeSupa([EMAIL_ROW, GATE_ROW, UNSYNTHESIZED]))
    assert {i["id"] for i in out["items"]} == {"e1", "g1", "u1"}
    assert out["counts"]["awaiting_approval"] == 2


def test_an_intake_is_titled_by_its_account_and_lines_not_as_blank_email():
    out = dashboard.intake_queue(FakeSupa([GATE_ROW]))
    item = out["items"][0]
    assert item["title"] == "Jarah Group LLC"
    assert item["subtitle"] == "General Liability, Worker's Compensation"


def test_an_email_keeps_its_subject_and_sender():
    item = dashboard.intake_queue(FakeSupa([EMAIL_ROW]))["items"][0]
    assert item["title"] == "Renewal question"
    assert item["subtitle"] == "jo@client.example"


def test_a_row_with_nothing_to_show_says_what_it_is_rather_than_inventing_a_title():
    item = dashboard.intake_queue(FakeSupa([UNSYNTHESIZED]))["items"][0]
    assert item["title"] == "(cowork)"
    assert item["actionable"] is False


def test_only_awaiting_approval_rows_are_actionable():
    out = dashboard.intake_queue(FakeSupa([GATE_ROW, UNSYNTHESIZED]))
    by_id = {i["id"]: i for i in out["items"]}
    assert by_id["g1"]["actionable"] is True
    assert by_id["u1"]["actionable"] is False


def test_the_email_card_still_filters_to_the_email_lanes():
    supa = FakeSupa([EMAIL_ROW, GATE_ROW])
    out = dashboard.email_queue(supa)
    assert [i["id"] for i in out["items"]] == ["e1"]
    assert supa.params["source"] == "in.(email-ms365)"


def test_the_email_card_keeps_its_original_field_names():
    """dashboard.html reads m.subject / m.from — renaming them would blank the card."""
    item = dashboard.email_queue(FakeSupa([EMAIL_ROW]))["items"][0]
    assert item["subject"] == "Renewal question"
    assert item["from"] == "jo@client.example"
