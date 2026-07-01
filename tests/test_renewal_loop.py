from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from hermes.renewals import loop
from hermes.renewals.momentum_mcp_client import MomentumMCPClient, MomentumMCPClientError


class FakeResponse:
    def __init__(self, status_code: int, body=None, *, text: str = "", headers=None, lines=None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or str(body or "")
        self.headers = headers or {"content-type": "application/json"}
        self._lines = lines or []
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._body

    def iter_lines(self, decode_unicode=True):
        _ = decode_unicode
        return iter(self._lines)


def _sample_record(**overrides):
    base = {
        "id": "r-1",
        "accountName": "Acme Trucking",
        "line_of_business": "Commercial Auto",
        "pipeline_stage": "Renewed - Won",
        "disposition": "won",
        "current_premium": 4200,
        "renewal_proposed_premium": 4300,
        "renewal_premium": 4400,
        "premium_change": 4.8,
        "momentum_client_id": "mc-123",
        "renewalWorksheet": {"lob_variant": "commercial_auto", "completion_type": "full_review"},
    }
    base.update(overrides)
    return base


def test_momentum_client_posts_bearer_jsonrpc():
    session = MagicMock()
    session.post.return_value = FakeResponse(200, {"result": {"noteId": "n1"}})
    client = MomentumMCPClient(base_url="https://mcp.example.com/mcp", api_key="abc", session=session)

    result = client.manage_notes({"databaseId": "mc-1", "note": "hello"})

    assert result["noteId"] == "n1"
    kwargs = session.post.call_args.kwargs
    assert kwargs["headers"]["Authorization"].startswith("Bearer ")
    assert kwargs["headers"]["Authorization"].endswith("abc")
    assert kwargs["json"]["method"] == "tools/call"
    assert kwargs["json"]["params"]["name"] == "manage_notes"


def test_momentum_client_parses_sse():
    session = MagicMock()
    session.post.return_value = FakeResponse(
        200,
        headers={"content-type": "text/event-stream"},
        lines=['data: {"result": {"noteId": "n9"}}', "data: [DONE]"],
    )
    client = MomentumMCPClient(base_url="https://mcp.example.com/mcp", api_key="abc", session=session)

    result = client.manage_notes({"databaseId": "mc-1", "note": "hello"})

    assert result["noteId"] == "n9"


def test_momentum_client_wraps_non_dict_result():
    session = MagicMock()
    session.post.return_value = FakeResponse(200, ["ok"])
    client = MomentumMCPClient(base_url="https://mcp.example.com/mcp", api_key="abc", session=session)

    result = client.manage_notes({"databaseId": "mc-1", "note": "hello"})

    assert result == {"result": ["ok"]}


def test_handle_disposition_webhook_logs_and_writes_back():
    supa = MagicMock()
    supa.upsert.side_effect = lambda table, payload, on_conflict="id": {"id": f"{table}-1", **payload}
    momentum = MagicMock()
    momentum.manage_notes.return_value = {"noteId": "note-1"}

    out = loop.handle_disposition_webhook({"event_uuid": "evt-1", "data": _sample_record()}, supa=supa, momentum=momentum)

    assert out["logged"] == 1
    tables = [call.args[0] for call in supa.upsert.call_args_list]
    assert tables[:5] == [
        "renewals_master",
        "renewal_events",
        "crm_sync_log",
        "crm_dispositions",
        "ams_writeback_log",
    ]
    update_payload = supa.update_where.call_args.args[1]
    assert update_payload["state"] == "succeeded"
    assert update_payload["posted_note_id"] == "note-1"


def test_handle_disposition_webhook_retries_retryable_failures():
    supa = MagicMock()
    supa.upsert.side_effect = lambda table, payload, on_conflict="id": {"id": f"{table}-1", **payload}
    momentum = MagicMock()
    momentum.manage_notes.side_effect = MomentumMCPClientError("temporary", retryable=True)

    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    out = loop.handle_disposition_webhook({"event_uuid": "evt-1", "data": _sample_record()}, supa=supa, momentum=momentum, now=now)

    assert out["writebacks"]["retrying"] == 1
    update_payload = supa.update_where.call_args.args[1]
    assert update_payload["state"] == "retrying"
    assert update_payload["next_retry_at"] == "2026-07-01T12:00:30+00:00"


def test_handle_worksheet_webhook_only_logs():
    supa = MagicMock()
    supa.upsert.side_effect = lambda table, payload, on_conflict="id": {"id": f"{table}-1", **payload}

    out = loop.handle_worksheet_webhook({"event_uuid": "evt-2", "data": _sample_record(disposition=None)}, supa=supa)

    assert out == {"received": 1, "logged": 1}
    tables = [call.args[0] for call in supa.upsert.call_args_list]
    assert "ams_writeback_log" not in tables


def test_run_reconcile_processes_due_rows_and_alerts_failed():
    supa = MagicMock()
    due = [{
        "event_uuid": "evt-3",
        "attempts": 1,
        "payload": {"renewal": _sample_record(), "note": {"databaseId": "mc-123", "note": "hi"}},
    }]
    failed = [{"renewal_id": "r-9", "attempts": 6, "last_error": "permanent"}]
    supa.select.side_effect = [due, failed]
    notifier = MagicMock()
    notifier_cls = MagicMock(return_value=notifier)
    momentum = MagicMock()
    momentum.manage_notes.return_value = {"noteId": "note-3"}

    out = loop.run_reconcile(supa=supa, momentum=momentum, notifier_cls=notifier_cls)

    assert out["attempted"] == 1
    assert out["succeeded"] == 1
    notifier.post_message.assert_called_once()
    assert "writeback failures" in notifier.post_message.call_args.kwargs["text"].lower()


def test_failed_digest_formats_missing_values():
    text = loop._failed_digest([{"attempts": 2}, {"renewal_id": "r-2", "last_error": "boom"}])

    assert "renewal_id: —" in text
    assert "attempts: 0" in text
    assert "renewal_id: r-2" in text
