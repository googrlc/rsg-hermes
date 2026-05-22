"""Tests for the Slack Events API webhook (/api/hermes/slack/crm-entry).

Covers signature verification, URL-verification challenge, message filtering,
Slack retry dedupe, and cross-transport dedupe with Socket Mode.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app
from hermes.core.dispatcher import DispatchResult

_SIGNING_SECRET = "test-signing-secret-do-not-use"


def _sign(body: bytes, ts: str) -> str:
    base = f"v0:{ts}:".encode("utf-8") + body
    digest = hmac.new(_SIGNING_SECRET.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def _post(client: TestClient, payload: dict, *, ts: str | None = None, signature: str | None = None):
    body = json.dumps(payload).encode("utf-8")
    ts = ts if ts is not None else str(int(time.time()))
    sig = signature if signature is not None else _sign(body, ts)
    return client.post(
        "/api/hermes/slack/crm-entry",
        content=body,
        headers={
            "content-type": "application/json",
            "x-slack-request-timestamp": ts,
            "x-slack-signature": sig,
        },
    )


def _event_callback(text: str = "Hermes:\nMODULE: intake\nfoo: bar", *, ts: str = "1700000000.000100", event_id: str = "Ev01ABCDEFG", user: str = "U_ADVISOR") -> dict:
    return {
        "type": "event_callback",
        "event_id": event_id,
        "event": {
            "type": "message",
            "channel": "C0B57E18RK5",
            "user": user,
            "text": text,
            "ts": ts,
        },
    }


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SLACK_EVENTS_SIGNING_SECRET", _SIGNING_SECRET)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("HERMES_BOT_USER_ID", "U_HERMES")

    import hermes.api as api_mod
    import hermes.integrations.slack_dedupe as dedupe_mod

    api_mod._espo = None
    api_mod._dispatcher = None
    api_mod._supa = None
    api_mod._slack_signature_verifier = None
    api_mod._slack_web_client = None
    dedupe_mod.reset_for_tests()
    yield
    api_mod._slack_signature_verifier = None
    api_mod._slack_web_client = None
    dedupe_mod.reset_for_tests()


@pytest.fixture
def client():
    return TestClient(app)


class TestSignatureVerification:
    def test_invalid_signature_returns_401(self, client) -> None:
        resp = _post(client, _event_callback(), signature="v0=deadbeef")
        assert resp.status_code == 401

    def test_stale_timestamp_returns_401(self, client) -> None:
        # SignatureVerifier defaults to 5 min tolerance; 10 min in the past must fail.
        stale = str(int(time.time()) - 600)
        resp = _post(client, _event_callback(), ts=stale)
        assert resp.status_code == 401

    def test_missing_signing_secret_returns_503(self, client, monkeypatch) -> None:
        monkeypatch.delenv("SLACK_EVENTS_SIGNING_SECRET", raising=False)
        import hermes.api as api_mod
        api_mod._slack_signature_verifier = None
        resp = _post(client, _event_callback())
        assert resp.status_code == 503

    def test_missing_headers_returns_401(self, client) -> None:
        """No X-Slack-Request-Timestamp / X-Slack-Signature → 401, not 500."""
        body = json.dumps(_event_callback()).encode("utf-8")
        resp = client.post(
            "/api/hermes/slack/crm-entry",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 401
        assert "Missing Slack signature headers" in resp.json()["detail"]

    def test_non_numeric_timestamp_returns_401(self, client) -> None:
        body = json.dumps(_event_callback()).encode("utf-8")
        resp = client.post(
            "/api/hermes/slack/crm-entry",
            content=body,
            headers={
                "content-type": "application/json",
                "x-slack-request-timestamp": "not-a-number",
                "x-slack-signature": "v0=deadbeef",
            },
        )
        assert resp.status_code == 401


class TestUrlVerification:
    def test_returns_challenge(self, client) -> None:
        resp = _post(client, {"type": "url_verification", "challenge": "abc123"})
        assert resp.status_code == 200
        assert resp.text == "abc123"


class TestFiltering:
    @patch("hermes.api._get_slack_web_client")
    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_non_crm_entry_channel_ignored(self, _espo, _disp, _web, client) -> None:
        payload = _event_callback()
        payload["event"]["channel"] = "C_OTHER"
        resp = _post(client, payload)
        assert resp.status_code == 200
        assert resp.json()["ignored"] == "wrong-channel"
        _disp.assert_not_called()

    @patch("hermes.api._get_slack_web_client")
    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_no_hermes_block_ignored(self, _espo, _disp, _web, client) -> None:
        resp = _post(client, _event_callback(text="hi team, no module here"))
        assert resp.status_code == 200
        assert resp.json()["ignored"] == "no-hermes-block"
        _disp.assert_not_called()

    @patch("hermes.api._get_slack_web_client")
    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_bot_message_ignored(self, _espo, _disp, _web, client) -> None:
        payload = _event_callback()
        payload["event"]["bot_id"] = "B_FIRST_APP"
        resp = _post(client, payload)
        assert resp.status_code == 200
        assert resp.json()["ignored"] == "bot-or-subtype"
        _disp.assert_not_called()

    @patch("hermes.api._get_slack_web_client")
    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_hermes_self_post_ignored(self, _espo, _disp, _web, client) -> None:
        payload = _event_callback(user="U_HERMES")
        resp = _post(client, payload)
        assert resp.status_code == 200
        assert resp.json()["ignored"] == "self-post"
        _disp.assert_not_called()


class TestHappyPath:
    @patch("hermes.api._get_slack_web_client")
    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_dispatches_and_posts_ack(self, mock_espo, mock_disp, mock_web, client) -> None:
        web = MagicMock()
        mock_web.return_value = web
        mock_disp.return_value.dispatch.return_value = DispatchResult(True, "Account created.")

        resp = _post(client, _event_callback())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["queued"] is True

        # Background task already ran (TestClient flushes them synchronously).
        mock_disp.return_value.dispatch.assert_called_once()
        web.chat_postMessage.assert_called_once()
        kwargs = web.chat_postMessage.call_args.kwargs
        assert kwargs["channel"] == "C0B57E18RK5"
        assert "Account created." in kwargs["text"]

    @patch("hermes.api._get_slack_web_client")
    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_passes_slack_blocks_to_chat_postMessage(self, mock_espo, mock_disp, mock_web, client) -> None:
        """When dispatcher returns data["slack_blocks"] (agency intake approval buttons),
        the webhook must attach them so users get clickable buttons in Slack."""
        web = MagicMock()
        mock_web.return_value = web
        approval_blocks = [
            {"type": "actions", "elements": [
                {"type": "button", "action_id": "agency_intake_approve_all", "text": {"type": "plain_text", "text": "APPROVE ALL"}},
                {"type": "button", "action_id": "agency_intake_cancel", "text": {"type": "plain_text", "text": "CANCEL"}},
            ]},
        ]
        mock_disp.return_value.dispatch.return_value = DispatchResult(
            True,
            "Intake draft ready — NOTHING WRITTEN YET.",
            {"draft_id": "abc-123", "slack_blocks": approval_blocks},
        )

        resp = _post(client, _event_callback())
        assert resp.status_code == 200

        web.chat_postMessage.assert_called_once()
        kwargs = web.chat_postMessage.call_args.kwargs
        assert kwargs.get("blocks") == approval_blocks
        assert "Intake draft ready" in kwargs["text"]


class TestDedupe:
    @patch("hermes.api._get_slack_web_client")
    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_slack_retry_same_event_id_ignored(self, _espo, mock_disp, mock_web, client) -> None:
        mock_web.return_value = MagicMock()
        mock_disp.return_value.dispatch.return_value = DispatchResult(True, "ok")

        first = _post(client, _event_callback(event_id="Ev_RETRY", ts="1700000001.000100"))
        assert first.status_code == 200
        assert first.json().get("queued") is True

        second = _post(client, _event_callback(event_id="Ev_RETRY", ts="1700000001.000100"))
        assert second.status_code == 200
        assert second.json()["ignored"] == "slack-retry"
        # Dispatcher only called once.
        assert mock_disp.return_value.dispatch.call_count == 1

    @patch("hermes.api._get_slack_web_client")
    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_cross_transport_dedupe(self, _espo, mock_disp, mock_web, client) -> None:
        """Socket Mode wins the race; webhook for same ts should skip."""
        from hermes.integrations.slack_dedupe import claim_event

        mock_web.return_value = MagicMock()
        mock_disp.return_value.dispatch.return_value = DispatchResult(True, "ok")

        # Simulate Socket Mode claiming the ts first.
        assert claim_event("crm_entry_ts:1700000002.000100") is True

        resp = _post(client, _event_callback(event_id="Ev_OTHER_APP", ts="1700000002.000100"))
        assert resp.status_code == 200
        assert resp.json()["ignored"] == "cross-transport-duplicate"
        mock_disp.return_value.dispatch.assert_not_called()
