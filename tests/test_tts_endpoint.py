"""Tests for the TTS endpoint in hermes-api."""

import os
from unittest import mock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HERMES_API_TOKEN", "")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("HERMES_SENTINEL_SLACK_CHANNEL", "C-TEST")


def test_tts_empty_text_rejected():
    """Empty text should return 400."""
    from hermes.api import app

    client = TestClient(app)
    resp = client.post("/api/hermes/tts", json={"text": ""})
    assert resp.status_code in (400, 422)


def test_tts_no_slack_token_rejected(monkeypatch):
    """Without SLACK_BOT_TOKEN the endpoint should fail before audio gen."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_SENTINEL_SLACK_CHANNEL", raising=False)
    from hermes.api import app

    client = TestClient(app)
    # With no channel and no bot token, should get a 400 or 503
    resp = client.post("/api/hermes/tts", json={"text": "hello"})
    assert resp.status_code in (400, 503)


def test_tts_success_with_mock(monkeypatch):
    """Full happy path with mocked audio generation and Slack upload."""
    from hermes.api import app

    # Mock edge_tts to return dummy audio
    mock_audio = b"FAKE_MP3_DATA"

    async def _fake_speak():
        return mock_audio

    mock_communicate = mock.MagicMock()
    mock_communicate.return_value.stream = mock.MagicMock()

    # Patch _generate_tts_audio directly
    with mock.patch("hermes.api._generate_tts_audio", return_value=mock_audio):
        with mock.patch("hermes.api._get_slack_web_client") as mock_slack:
            mock_client = mock.MagicMock()
            mock_slack.return_value = mock_client
            client = TestClient(app)
            resp = client.post("/api/hermes/tts", json={"text": "test message"})
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
            mock_client.files_upload_v2.assert_called_once()


def test_tts_generation_failure_returns_502(monkeypatch):
    """When TTS generation fails, should return 502."""
    from hermes.api import app

    with mock.patch("hermes.api._generate_tts_audio", return_value=None):
        client = TestClient(app)
        resp = client.post("/api/hermes/tts", json={"text": "test"})
        assert resp.status_code == 502
