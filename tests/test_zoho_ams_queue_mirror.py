"""Zoho AMS_Write_Queue → outbound_sync_queue mirror."""

from __future__ import annotations

from unittest.mock import MagicMock

from hermes.sync.zoho_ams_queue import (
    already_mirrored,
    is_mirrorable,
    parse_payload,
    run_zoho_ams_queue_mirror,
    to_outbound_row,
    zoho_queue_id,
)

APPROVED = {
    "id": "z-q-1",
    "Queue_ID": "11111111-1111-1111-1111-111111111111",
    "Object_Type": "renewal",
    "Object_ID": "CPP1",
    "Destination": "nowcerts",
    "Action": "create",
    "Status": "queued",
    "Approved_By": "producer@example.com",
    "Approved_At": "2026-08-18T12:00:00+00:00",
    "Payload": (
        '{"action":"request_terms","renewal_id":"ren-1",'
        '"policy_number":"CPP1","expected_result":"terms requested"}'
    ),
}


def test_parse_payload_accepts_string_or_dict():
    assert parse_payload(APPROVED["Payload"])["action"] == "request_terms"
    assert parse_payload({"action": "prepare_options"})["action"] == "prepare_options"
    assert parse_payload("not-json") == {}


def test_is_mirrorable_requires_approval_and_executor_action():
    ok, _ = is_mirrorable(APPROVED)
    assert ok
    missing = dict(APPROVED, Approved_By="")
    ok, reason = is_mirrorable(missing)
    assert not ok and "Approved_By" in reason
    needs = dict(APPROVED, Status="needs_approval")
    ok, reason = is_mirrorable(needs)
    assert not ok and "queued" in reason
    other = dict(APPROVED, Object_Type="intake")
    ok, _ = is_mirrorable(other)
    assert not ok
    bad_action = dict(APPROVED, Payload='{"action":"bind","renewal_id":"ren-1","expected_result":"x"}')
    ok, reason = is_mirrorable(bad_action)
    assert not ok and "executor action" in reason
    no_result = dict(APPROVED, Payload='{"action":"request_terms","renewal_id":"ren-1"}')
    ok, reason = is_mirrorable(no_result)
    assert not ok and "expected_result" in reason


def test_to_outbound_row_is_executor_shaped():
    row = to_outbound_row(APPROVED, renewal_id="ren-1", policy_number="CPP1")
    assert row["object_type"] == "renewal"
    assert row["destination_system"] == "nowcerts"
    assert row["status"] == "queued"
    assert row["action"] == "create"
    assert row["approved_by"] == "producer@example.com"
    assert row["payload"]["action"] == "request_terms"
    assert row["payload"]["expected_result"] == "terms requested"
    assert row["payload"]["zoho_queue_id"] == APPROVED["Queue_ID"]
    update = dict(
        APPROVED,
        Payload='{"action":"update_ams","renewal_id":"ren-1","policy_number":"CPP1","expected_result":"ams updated"}',
    )
    assert to_outbound_row(update, renewal_id="ren-1", policy_number="CPP1")["action"] == "update"


def test_already_mirrored_on_zoho_queue_id_or_open_work():
    existing = [{
        "status": "queued",
        "object_id": "CPP1",
        "object_type": "renewal",
        "action": "create",
        "payload": {"zoho_queue_id": APPROVED["Queue_ID"]},
    }]
    assert already_mirrored(
        existing, zoho_id=APPROVED["Queue_ID"], policy_number="CPP1", queue_action="create"
    )
    other = [{
        "status": "queued",
        "object_id": "CPP1",
        "object_type": "renewal",
        "action": "create",
        "payload": {"zoho_queue_id": "other"},
    }]
    assert already_mirrored(
        other, zoho_id="brand-new", policy_number="CPP1", queue_action="create"
    )
    done = [{
        "status": "completed",
        "object_id": "CPP1",
        "object_type": "renewal",
        "action": "create",
        "payload": {"zoho_queue_id": "other"},
    }]
    assert not already_mirrored(
        done, zoho_id="brand-new", policy_number="CPP1", queue_action="create"
    )


class FakeZoho:
    def __init__(self, rows):
        self.rows = rows

    def iter_records(self, module, *, criteria=None, **kwargs):
        return iter(self.rows)


def test_mirror_inserts_once_then_skips():
    supa = MagicMock()
    supa.select.side_effect = [
        [{"id": "ren-1", "policy_number": "CPP1"}],  # resolve renewal
        [],  # existing queue rows
    ]
    result = run_zoho_ams_queue_mirror(supa=supa, zoho=FakeZoho([APPROVED]), dry_run=False)
    assert result.ok
    assert result.scanned == 1
    assert result.mirrored == 1
    table, payload = supa.insert.call_args.args
    assert table == "outbound_sync_queue"
    assert payload["payload"]["zoho_queue_id"] == APPROVED["Queue_ID"]
    assert payload["payload"]["renewal_id"] == "ren-1"


def test_mirror_skips_already_queued_and_reports_unresolved():
    supa = MagicMock()
    supa.select.side_effect = [
        [{"id": "ren-1", "policy_number": "CPP1"}],
        [{
            "status": "queued",
            "object_id": "CPP1",
            "object_type": "renewal",
            "action": "create",
            "payload": {"zoho_queue_id": APPROVED["Queue_ID"]},
        }],
        [],  # orphan: renewal_id lookup
        [],  # orphan: policy_number lookup
    ]
    dup, orphan = dict(APPROVED), dict(
        APPROVED,
        id="z-q-2",
        Queue_ID="22222222-2222-2222-2222-222222222222",
        Payload='{"action":"request_terms","renewal_id":"missing","policy_number":"NOPE","expected_result":"x"}',
    )
    result = run_zoho_ams_queue_mirror(
        supa=supa, zoho=FakeZoho([dup, orphan]), dry_run=False
    )
    assert result.mirrored == 0
    assert result.skipped == 1
    assert result.errors
    assert not supa.insert.called
    assert zoho_queue_id(APPROVED) == APPROVED["Queue_ID"]
